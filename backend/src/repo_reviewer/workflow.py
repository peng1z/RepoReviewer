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
    clear_uncitable_lines,
    coerce_comment_payload,
    normalize_comments,
    parse_json_response,
    structured_completion,
)
from .repository import collect_repo_files, detect_key_files, extract_snippet, prioritize_files


OVER_CAP_REASON = (
    "not reviewed: max_files limit of {limit} reached "
    "({eligible} files were eligible)"
)

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
        over_cap = []
    else:
        selected, over_cap = prioritize_files(accepted_files, state.request.max_files)
        skipped.extend(
            (path, OVER_CAP_REASON.format(limit=state.request.max_files, eligible=len(accepted_files)))
            for path in over_cap
        )

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
    partial_files: list[tuple[str, int, int]] = []
    total = max(1, len(state.files_to_review))
    for index, file_name in enumerate(state.files_to_review, start=1):
        path = repo_root / file_name
        full_text = path.read_text(encoding="utf-8", errors="ignore")
        content, truncated_from = _bounded_content(full_text, state.request.max_file_bytes)
        if truncated_from:
            # Say so in the prompt. Without this the model sees a file that
            # stops mid-statement and reports the cut as a defect in the code:
            # in a review of psf/requests, four of eleven high-severity
            # findings were the reviewer describing this pipeline's own
            # truncation as "incomplete method", "truncated", "syntax error".
            content += (
                f"\n\n[TRUNCATED: showing the first {len(content)} characters of "
                f"{truncated_from}. The file continues past this point; do not "
                f"report the cut-off as a defect.]"
            )
            partial_files.append((file_name, len(content), truncated_from))
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
        # The model cites positions in the file it was shown, and sometimes
        # cites positions that cannot hold what it describes -- past the end of
        # the file, or blank. Checked against the content actually sent, since a
        # line past a truncation is one the model never read.
        uncitable = clear_uncitable_lines(parsed, {file_name: content.splitlines()})
        for comment in parsed:
            comment.snippet = extract_snippet(path, comment.line, radius=6)
        comments.extend(parsed)
        percent = 35 + int(index / total * 40)
        note = f"Reviewed {file_name}"
        if dropped:
            note += f" ({len(dropped)} malformed finding(s) skipped: {dropped[0]})"
        if uncitable:
            note += f" ({len(uncitable)} unusable line number(s) cleared)"
        if truncated_from:
            note += f" (partial: {len(content)} of {truncated_from} characters)"
        await emit(
            progress,
            ProgressEvent(stage="review", message=note, percent=min(percent, 75)),
        )
    state.comments = comments
    state.skipped_files.extend(
        SkippedFile(
            path=name,
            reason=(
                f"partially reviewed: {shown} of {total_chars} characters were sent to the model"
            ),
        )
        for name, shown, total_chars in partial_files
    )
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
        skipped_notes=_skipped_notes(state),
    )
    await emit(progress, ProgressEvent(stage="summary", message="Prepared final summary", percent=95))
    return state


def _bounded_content(text: str, max_file_bytes: int) -> tuple[str, int | None]:
    """Cut a file to the size the request already asked for, and say if it cut.

    The review used to truncate at a hard-coded 12,000 characters while the
    collector accepted anything up to `max_file_bytes` (40,000 by default).
    Every file between the two was reviewed on part of its content, with
    nothing said to the model or to the reader -- psf/requests' adapters.py is
    748 lines and roughly the first 300 were ever seen.

    Deriving the bound from the same setting that governs collection means a
    file the collector accepted is now reviewed whole. The cut path is kept for
    a caller that bypasses collection, and reports what it did.
    """
    if len(text) <= max_file_bytes:
        return text, None
    return text[:max_file_bytes], len(text)


def _skipped_notes(state: ReviewState) -> list[str]:
    notes = [
        "Generated, binary, oversized, and ignored files were excluded from review.",
        "PR reviews focus comments on changed files while using broader repo context.",
    ]
    partial = sum(1 for item in state.skipped_files if item.reason.startswith("partially reviewed"))
    if partial:
        notes.insert(
            0,
            f"{partial} file(s) were larger than max_file_bytes and were reviewed on a prefix "
            "of their content, not in full.",
        )
    over_cap = sum(
        1 for item in state.skipped_files if item.reason.startswith("not reviewed: max_files")
    )
    if over_cap:
        reviewed = len(state.files_to_review)
        notes.insert(
            0,
            f"Coverage: {reviewed} of {reviewed + over_cap} eligible files were reviewed. "
            f"The other {over_cap} were ranked below the max_files cut and never opened.",
        )
    return notes


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
