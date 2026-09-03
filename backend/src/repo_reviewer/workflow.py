from __future__ import annotations

import json
from pathlib import Path
from typing import Awaitable, Callable

from langgraph.graph import END, StateGraph

from .config import Settings, load_repo_config
from .github_client import checkout_pr_head, clone_repo, fetch_pr_changed_files, parse_github_url
from .models import ProgressEvent, ProjectContext, ReviewComment, ReviewRequest, ReviewState, ReviewSummary, SkippedFile
from .prompts import CONTEXT_SYSTEM_PROMPT, REVIEW_SYSTEM_PROMPT, SUMMARY_SYSTEM_PROMPT
from .provider import (
    build_comments,
    coerce_comment_payload,
    normalize_comments,
    parse_json_response,
    structured_completion,
)
from .repository import collect_repo_files, detect_key_files, extract_snippet, prioritize_files


ProgressCallback = Callable[[ProgressEvent], Awaitable[None]]


def _coerce_string_list(value, *, fallback: list[str] | None = None) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, str):
        return [value]
    return fallback or []


def _coerce_string(value, *, fallback: str) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return fallback
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=True)
    return str(value)


async def emit(progress: ProgressCallback | None, event: ProgressEvent) -> None:
    if progress:
        await progress(event)


async def cloner_agent(state: ReviewState, progress: ProgressCallback | None = None) -> ReviewState:
    repo_name = parse_github_url(str(state.request.github_url)).repo
    workspace = Path(".cache") / "repo-reviewer" / repo_name
    clone_repo(str(state.request.github_url), workspace)
    if state.request.mode == "pr" and state.request.pr_number is not None:
        settings = Settings()
        checkout_pr_head(str(state.request.github_url), workspace, state.request.pr_number, settings.github_token)
    state.repo_name = repo_name
    state.workspace_dir = str(workspace)
    await emit(progress, ProgressEvent(stage="cloner", message=f"Cloned {repo_name}", percent=15))
    return state


async def context_agent(state: ReviewState, progress: ProgressCallback | None = None) -> ReviewState:
    assert state.workspace_dir
    repo_root = Path(state.workspace_dir)
    resolved_config = load_repo_config(repo_root)
    accepted_files, skipped = collect_repo_files(
        repo_root,
        ignore_globs=resolved_config.data["ignore_globs"],
        include_tests=state.request.include_tests,
        max_file_bytes=state.request.max_file_bytes,
    )

    if state.request.mode == "pr" and state.request.pr_number is not None:
        pr_files = set(
            fetch_pr_changed_files(
                str(state.request.github_url),
                state.request.pr_number,
                Settings().github_token,
            )
        )
        selected = [path for path in accepted_files if path.as_posix() in pr_files]
    else:
        selected = prioritize_files(accepted_files, state.request.max_files)

    state.files_to_review = [path.as_posix() for path in selected]
    state.skipped_files = [SkippedFile(path=path.as_posix(), reason=reason) for path, reason in skipped]

    readme_text = ""
    for candidate in ("README.md", "README"):
        readme_path = repo_root / candidate
        if readme_path.exists():
            readme_text = readme_path.read_text(encoding="utf-8", errors="ignore")[:5000]
            break

    structure_preview = "\n".join(sorted({path.parts[0] for path in accepted_files[:80]}))
    key_files = detect_key_files(repo_root, selected)
    key_file_previews: list[str] = []
    for key_file in key_files[:5]:
        path = repo_root / key_file
        if path.exists():
            preview = path.read_text(encoding="utf-8", errors="ignore")[:1500]
            key_file_previews.append(f"FILE: {key_file}\n{preview}")
    key_previews_text = "\n\n".join(key_file_previews) or "No key files detected."

    summary_text = await structured_completion(
        provider=state.request.provider,
        model=state.request.model,
        system_prompt=CONTEXT_SYSTEM_PROMPT,
        user_prompt=(
            f"README:\n{readme_text or 'No README found.'}\n\n"
            f"Folder structure:\n{structure_preview or 'No structure available.'}\n\n"
            f"Key file previews:\n{key_previews_text}\n\n"
            "Respond in JSON with keys readme_summary, folder_summary, architecture_notes."
        ),
    )
    try:
        parsed = parse_json_response(summary_text)
    except (ValueError, json.JSONDecodeError):
        parsed = {
            "readme_summary": "README summary unavailable due to non-JSON model output.",
            "folder_summary": structure_preview or "Folder summary unavailable.",
            "architecture_notes": key_files[:5],
        }
    state.project_context = ProjectContext(
        readme_summary=_coerce_string(parsed.get("readme_summary"), fallback="README summary unavailable."),
        folder_summary=_coerce_string(parsed.get("folder_summary"), fallback="Folder summary unavailable."),
        key_files=key_files,
        architecture_notes=_coerce_string_list(parsed.get("architecture_notes"), fallback=key_files[:5]),
    )
    await emit(
        progress,
        ProgressEvent(stage="context", message=f"Prepared context for {len(state.files_to_review)} files", percent=35),
    )
    return state


async def review_agent(state: ReviewState, progress: ProgressCallback | None = None) -> ReviewState:
    assert state.workspace_dir
    assert state.project_context
    repo_root = Path(state.workspace_dir)
    comments: list[ReviewComment] = []
    total = max(1, len(state.files_to_review))
    for index, file_name in enumerate(state.files_to_review, start=1):
        path = repo_root / file_name
        content = path.read_text(encoding="utf-8", errors="ignore")[:12_000]
        response = await structured_completion(
            provider=state.request.provider,
            model=state.request.model,
            system_prompt=REVIEW_SYSTEM_PROMPT,
            user_prompt=(
                f"Repository context:\n{state.project_context.model_dump_json(indent=2)}\n\n"
                f"Review this file:\nPATH: {file_name}\n\nCONTENT:\n{content}\n\n"
                "Return JSON only."
            ),
        )
        try:
            raw_comments = coerce_comment_payload(parse_json_response(response))
        except (ValueError, json.JSONDecodeError):
            raw_comments = []
        # Only one file was sent, so a finding that omits `file` belongs to it.
        parsed, dropped = build_comments(raw_comments, default_file=file_name)
        for comment in parsed:
            comment.snippet = extract_snippet(path, comment.line, radius=6)
        comments.extend(parsed)
        percent = 35 + int(index / total * 40)
        note = f"Reviewed {file_name}"
        if dropped:
            note += f" ({len(dropped)} malformed finding(s) skipped: {dropped[0]})"
        await emit(
            progress,
            ProgressEvent(stage="review", message=note, percent=min(percent, 75)),
        )
    state.comments = comments
    return state


async def priority_agent(state: ReviewState, progress: ProgressCallback | None = None) -> ReviewState:
    state.comments = normalize_comments(state.comments)
    await emit(
        progress,
        ProgressEvent(stage="priority", message=f"Ranked {len(state.comments)} findings", percent=85),
    )
    return state


async def summary_agent(state: ReviewState, progress: ProgressCallback | None = None) -> ReviewState:
    assert state.project_context
    response = await structured_completion(
        provider=state.request.provider,
        model=state.request.model,
        system_prompt=SUMMARY_SYSTEM_PROMPT,
        user_prompt=(
            f"Context:\n{state.project_context.model_dump_json(indent=2)}\n\n"
            f"Findings:\n{json.dumps([comment.model_dump() for comment in state.comments], indent=2)}\n\n"
            f"Skipped files:\n{json.dumps([item.model_dump() for item in state.skipped_files], indent=2)}\n\n"
            "Respond in JSON with keys headline, top_findings."
        ),
    )
    try:
        parsed = parse_json_response(response)
    except (ValueError, json.JSONDecodeError):
        parsed = {
            "headline": "Review complete. Summary model output was not valid JSON.",
            "top_findings": [
                f"{comment.severity.title()}: {comment.issue}" for comment in state.comments[:5]
            ] or ["No actionable findings were returned."],
        }
    state.summary = ReviewSummary(
        headline=parsed.get("headline", "Review complete."),
        top_findings=_coerce_string_list(parsed.get("top_findings"), fallback=["No actionable findings were returned."])[:5],
        skipped_notes=[
            "Generated, binary, oversized, and ignored files were excluded from review.",
            "PR reviews focus comments on changed files while using broader repo context.",
        ],
    )
    await emit(progress, ProgressEvent(stage="summary", message="Prepared final summary", percent=95))
    return state


def build_graph(progress: ProgressCallback | None = None):
    async def cloner_node(state: ReviewState) -> ReviewState:
        return await cloner_agent(state, progress)

    async def context_node(state: ReviewState) -> ReviewState:
        return await context_agent(state, progress)

    async def review_node(state: ReviewState) -> ReviewState:
        return await review_agent(state, progress)

    async def priority_node(state: ReviewState) -> ReviewState:
        return await priority_agent(state, progress)

    async def summary_node(state: ReviewState) -> ReviewState:
        return await summary_agent(state, progress)

    graph = StateGraph(ReviewState)
    graph.add_node("cloner", cloner_node)
    graph.add_node("context", context_node)
    graph.add_node("review", review_node)
    graph.add_node("priority", priority_node)
    graph.add_node("summary", summary_node)
    graph.set_entry_point("cloner")
    graph.add_edge("cloner", "context")
    graph.add_edge("context", "review")
    graph.add_edge("review", "priority")
    graph.add_edge("priority", "summary")
    graph.add_edge("summary", END)
    return graph.compile()
