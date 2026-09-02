from __future__ import annotations

import asyncio
import ast
import csv
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any, Literal

from pydantic import BaseModel, Field

from .markdown import render_review_markdown
from .models import (
    ProjectContext,
    ReviewArtifacts,
    ReviewComment,
    ReviewRequest,
    ReviewResult,
    ReviewSummary,
    SkippedFile,
)
from .prompts import REVIEW_SYSTEM_PROMPT
from .provider import normalize_comments, parse_json_response, structured_completion
from .repository import extract_snippet
from .workflow import cloner_agent, context_agent, review_agent, summary_agent


EvaluationMethod = Literal["full", "single_agent", "no_context", "no_priority"]


class EvaluationRepo(BaseModel):
    github_url: str
    pr_number: int | None = None
    label: str | None = None
    include_tests: bool = True
    max_files: int = 30
    max_file_bytes: int = 40_000


class EvaluationDataset(BaseModel):
    name: str
    provider: str = "openai"
    model: str = "openai/gpt-4.1-mini"
    methods: list[EvaluationMethod] = Field(default_factory=lambda: ["full", "single_agent", "no_context", "no_priority"])
    repos: list[EvaluationRepo]


class EvaluationRow(BaseModel):
    run_id: str
    repo_name: str
    mode: str
    method: str
    provider: str
    model: str
    total_findings: int
    high_findings: int
    medium_findings: int
    low_findings: int
    runtime_seconds: float
    estimated_cost_usd: float | None = None
    artifact_dir: str
    notes: str = ""


@dataclass(slots=True)
class PreparedRun:
    request: ReviewRequest
    repo_name: str
    workspace_dir: Path
    files_to_review: list[str]
    skipped_files: list[SkippedFile]
    project_context: ProjectContext


def load_dataset(path: Path) -> EvaluationDataset:
    data = json.loads(path.read_text(encoding="utf-8"))
    return EvaluationDataset.model_validate(data)


def export_rows(rows: list[EvaluationRow], destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(EvaluationRow.model_fields.keys())
    with destination.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row.model_dump())


def export_annotation_sheet(experiment_dir: Path) -> Path:
    runs_dir = experiment_dir / "runs"
    destination = experiment_dir / "annotation-sheet.csv"
    fieldnames = [
        "run_id",
        "repo_name",
        "method",
        "provider",
        "model",
        "file",
        "line",
        "severity",
        "issue",
        "suggestion",
        "correctness",
        "actionability",
        "severity_calibration",
        "duplication",
        "scope",
        "top5_usefulness",
        "annotator_notes",
    ]
    with destination.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for run_file in sorted(runs_dir.glob("*.json")):
            payload = json.loads(run_file.read_text(encoding="utf-8"))
            row = payload["row"]
            result = payload["result"]
            for comment in result["comments"]:
                writer.writerow(
                    {
                        "run_id": row["run_id"],
                        "repo_name": row["repo_name"],
                        "method": row["method"],
                        "provider": row["provider"],
                        "model": row["model"],
                        "file": comment["file"],
                        "line": comment["line"],
                        "severity": comment["severity"],
                        "issue": comment["issue"],
                        "suggestion": comment["suggestion"],
                        "correctness": "",
                        "actionability": "",
                        "severity_calibration": "",
                        "duplication": "",
                        "scope": "",
                        "top5_usefulness": "",
                        "annotator_notes": "",
                    }
                )
    return destination


async def run_evaluation_dataset(
    dataset: EvaluationDataset,
    *,
    output_root: Path,
) -> tuple[Path, list[EvaluationRow]]:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    experiment_dir = output_root / f"{dataset.name}-{stamp}"
    experiment_dir.mkdir(parents=True, exist_ok=True)
    (experiment_dir / "dataset.json").write_text(json.dumps(dataset.model_dump(mode="json"), indent=2), encoding="utf-8")

    rows: list[EvaluationRow] = []
    for repo in dataset.repos:
        for method in dataset.methods:
            row = await _run_single_experiment(repo, dataset, method, experiment_dir)
            rows.append(row)

    export_rows(rows, experiment_dir / "results.csv")
    export_annotation_sheet(experiment_dir)
    return experiment_dir, rows


async def _run_single_experiment(
    repo: EvaluationRepo,
    dataset: EvaluationDataset,
    method: EvaluationMethod,
    experiment_dir: Path,
) -> EvaluationRow:
    request = ReviewRequest(
        github_url=repo.github_url,
        pr_number=repo.pr_number,
        provider=dataset.provider,  # type: ignore[arg-type]
        model=dataset.model,
        max_files=repo.max_files,
        max_file_bytes=repo.max_file_bytes,
        include_tests=repo.include_tests,
        output_root=str(experiment_dir / "artifacts"),
    )

    started = perf_counter()
    if method == "full":
        result = await _run_full(request)
        notes = "Full multi-agent pipeline."
    elif method == "single_agent":
        result = await _run_single_agent_baseline(request)
        notes = "Single-agent baseline with one combined review prompt."
    elif method == "no_context":
        result = await _run_without_context(request)
        notes = "Context prompt disabled before file review."
    elif method == "no_priority":
        result = await _run_without_priority(request)
        notes = "PriorityAgent disabled; comments left in review order."
    else:  # pragma: no cover
        raise ValueError(f"Unsupported evaluation method: {method}")
    elapsed = perf_counter() - started

    run_id = f"{result.repo_name}-{method}-{datetime.now(timezone.utc).strftime('%H%M%S')}"
    summary = _severity_counts(result.comments)
    row = EvaluationRow(
        run_id=run_id,
        repo_name=result.repo_name,
        mode=result.mode,
        method=method,
        provider=result.provider,
        model=result.model,
        total_findings=len(result.comments),
        high_findings=summary["high"],
        medium_findings=summary["medium"],
        low_findings=summary["low"],
        runtime_seconds=round(elapsed, 3),
        artifact_dir=str(Path(result.artifacts.json_path).parent),
        notes=notes,
    )
    _write_row_summary(experiment_dir / "runs", run_id, row, result)
    return row


async def _prepare_run(request: ReviewRequest) -> PreparedRun:
    from .models import ReviewState

    state = ReviewState(request=request)
    state = await cloner_agent(state)
    state = await context_agent(state)
    assert state.repo_name
    assert state.workspace_dir
    assert state.project_context
    return PreparedRun(
        request=request,
        repo_name=state.repo_name,
        workspace_dir=Path(state.workspace_dir),
        files_to_review=state.files_to_review,
        skipped_files=state.skipped_files,
        project_context=state.project_context,
    )


async def _run_full(request: ReviewRequest) -> ReviewResult:
    from .service import run_review

    return await run_review(request)


async def _run_without_context(request: ReviewRequest) -> ReviewResult:
    prepared = await _prepare_run(request)
    blank_context = ProjectContext(
        readme_summary="ContextAgent disabled for ablation.",
        folder_summary="ContextAgent disabled for ablation.",
        key_files=[],
        architecture_notes=[],
    )
    comments = await _review_files(
        prepared=prepared,
        context=blank_context,
        single_agent=False,
    )
    comments = normalize_comments(comments)
    summary = await _summarize(prepared, comments, blank_context)
    return _save_result(
        prepared=prepared,
        method="no_context",
        comments=comments,
        context=blank_context,
        summary=summary,
    )


async def _run_without_priority(request: ReviewRequest) -> ReviewResult:
    prepared = await _prepare_run(request)
    comments = await _review_files(
        prepared=prepared,
        context=prepared.project_context,
        single_agent=False,
    )
    summary = await _summarize(prepared, comments, prepared.project_context)
    return _save_result(
        prepared=prepared,
        method="no_priority",
        comments=comments,
        context=prepared.project_context,
        summary=summary,
    )


async def _run_single_agent_baseline(request: ReviewRequest) -> ReviewResult:
    prepared = await _prepare_run(request)
    comments = await _review_files(
        prepared=prepared,
        context=prepared.project_context,
        single_agent=True,
    )
    comments = normalize_comments(comments)
    summary = await _summarize(prepared, comments, prepared.project_context)
    return _save_result(
        prepared=prepared,
        method="single_agent",
        comments=comments,
        context=prepared.project_context,
        summary=summary,
    )


async def _review_files(
    *,
    prepared: PreparedRun,
    context: ProjectContext,
    single_agent: bool,
) -> list[ReviewComment]:
    if single_agent:
        return await single_agent_review(prepared, context)

    from .models import ReviewState

    state = ReviewState(
        request=prepared.request,
        workspace_dir=str(prepared.workspace_dir),
        repo_name=prepared.repo_name,
        files_to_review=prepared.files_to_review,
        skipped_files=prepared.skipped_files,
        project_context=context,
    )
    state = await review_agent(state)
    return normalize_comments(state.comments)


async def single_agent_review(prepared: PreparedRun, context: ProjectContext) -> list[ReviewComment]:
    file_blocks: list[str] = []
    for file_name in prepared.files_to_review:
        path = prepared.workspace_dir / file_name
        content = path.read_text(encoding="utf-8", errors="ignore")[:8_000]
        file_blocks.append(f"FILE: {file_name}\n{content}")

    response = await structured_completion(
        provider=prepared.request.provider,
        model=prepared.request.model,
        system_prompt=REVIEW_SYSTEM_PROMPT,
        user_prompt=(
            f"Repository context:\n{context.model_dump_json(indent=2)}\n\n"
            "Review the following repository files in one pass and return a JSON list of findings.\n\n"
            + "\n\n".join(file_blocks)
        ),
    )
    try:
        raw_comments = parse_json_response(response)
    except (ValueError, json.JSONDecodeError):
        raw_comments = []
    comments: list[ReviewComment] = []
    for item in raw_comments:
        comment = ReviewComment.model_validate(item)
        path = prepared.workspace_dir / comment.file if comment.file else None
        if path and path.exists():
            comment.snippet = extract_snippet(path, comment.line, radius=6)
        comments.append(comment)
    return comments


async def _summarize(prepared: PreparedRun, comments: list[ReviewComment], context: ProjectContext) -> ReviewSummary:
    from .models import ReviewState

    state = ReviewState(
        request=prepared.request,
        workspace_dir=str(prepared.workspace_dir),
        repo_name=prepared.repo_name,
        files_to_review=prepared.files_to_review,
        skipped_files=prepared.skipped_files,
        project_context=context,
        comments=comments,
    )
    state = await summary_agent(state)
    assert state.summary
    state.summary.top_findings = _normalize_top_findings(state.summary.top_findings)
    return state.summary


def _save_result(
    *,
    prepared: PreparedRun,
    method: str,
    comments: list[ReviewComment],
    context: ProjectContext,
    summary: ReviewSummary,
) -> ReviewResult:
    output_dir = Path(prepared.request.output_root) / f"{prepared.repo_name}-{method}"
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "review.json"
    markdown_path = output_dir / "review.md"
    result = ReviewResult(
        repo_url=prepared.request.github_url,
        repo_name=prepared.repo_name,
        mode=prepared.request.mode,
        provider=prepared.request.provider,
        model=prepared.request.model,
        context=context,
        comments=comments,
        skipped_files=prepared.skipped_files,
        summary=summary,
        artifacts=ReviewArtifacts(
            json_path=str(json_path.resolve()),
            markdown_path=str(markdown_path.resolve()),
        ),
    )
    json_path.write_text(json.dumps(result.model_dump(mode="json"), indent=2), encoding="utf-8")
    markdown_path.write_text(render_review_markdown(result), encoding="utf-8")
    return result


def _write_row_summary(destination: Path, run_id: str, row: EvaluationRow, result: ReviewResult) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    payload = {
        "row": row.model_dump(mode="json"),
        "result": result.model_dump(mode="json"),
    }
    (destination / f"{run_id}.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _severity_counts(comments: list[ReviewComment]) -> dict[str, int]:
    counts = {"high": 0, "medium": 0, "low": 0}
    for comment in comments:
        counts[comment.severity] += 1
    return counts


def _normalize_top_findings(values: list[Any]) -> list[str]:
    normalized: list[str] = []
    for value in values:
        if isinstance(value, str):
            stripped = value.strip()
            if stripped.startswith("{") and stripped.endswith("}"):
                try:
                    parsed = ast.literal_eval(stripped)
                except (SyntaxError, ValueError):
                    normalized.append(value)
                    continue
                if isinstance(parsed, dict):
                    value = parsed
                else:
                    normalized.append(value)
                    continue
            else:
                normalized.append(value)
                continue
        if isinstance(value, dict):
            issue = str(value.get("issue", "")).strip()
            severity = str(value.get("severity", "")).strip()
            file_name = str(value.get("file", "")).strip()
            prefix = f"{severity.title()}: " if severity else ""
            if issue and file_name:
                normalized.append(f"{prefix}{file_name}: {issue}")
            elif issue:
                normalized.append(f"{prefix}{issue}")
            else:
                normalized.append(json.dumps(value))
            continue
        normalized.append(str(value))
    return normalized


def run_evaluation_sync(dataset_path: Path, output_root: Path) -> tuple[Path, list[EvaluationRow]]:
    dataset = load_dataset(dataset_path)
    return asyncio.run(run_evaluation_dataset(dataset, output_root=output_root))
