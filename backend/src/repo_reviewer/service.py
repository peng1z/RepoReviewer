from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .markdown import render_review_markdown
from .models import ProgressEvent, ReviewArtifacts, ReviewJob, ReviewRequest, ReviewResult, ReviewState
from .workflow import build_graph


class ReviewJobStore:
    def __init__(self) -> None:
        self.jobs: dict[str, ReviewJob] = {}
        self.events: dict[str, list[ProgressEvent]] = defaultdict(list)

    def create(self, request: ReviewRequest) -> ReviewJob:
        job = ReviewJob(id=uuid4().hex, request=request)
        self.jobs[job.id] = job
        return job

    def append_event(self, job_id: str, event: ProgressEvent) -> None:
        self.events[job_id].append(event)

    def list_events(self, job_id: str) -> list[ProgressEvent]:
        return self.events.get(job_id, [])

    def get(self, job_id: str) -> ReviewJob:
        return self.jobs[job_id]


async def run_review(
    request: ReviewRequest,
    *,
    progress_callback=None,
) -> ReviewResult:
    state = ReviewState(request=request)
    graph = build_graph(progress_callback)
    final_state = await graph.ainvoke(state)
    repo_name = final_state["repo_name"]
    output_dir = _prepare_output_dir(request.output_root, repo_name)
    json_path = output_dir / "review.json"
    markdown_path = output_dir / "review.md"

    result = ReviewResult(
        repo_url=request.github_url,
        repo_name=repo_name,
        mode=request.mode,
        provider=request.provider,
        model=request.model,
        context=final_state["project_context"],
        comments=final_state["comments"],
        skipped_files=final_state["skipped_files"],
        summary=final_state["summary"],
        artifacts=ReviewArtifacts(
            json_path=str(json_path.resolve()),
            markdown_path=str(markdown_path.resolve()),
        ),
    )

    json_path.write_text(json.dumps(result.model_dump(mode="json"), indent=2), encoding="utf-8")
    markdown_path.write_text(render_review_markdown(result), encoding="utf-8")
    if progress_callback:
        await progress_callback(ProgressEvent(stage="done", message="Artifacts saved", percent=100))
    return result


def _prepare_output_dir(output_root: str, repo_name: str) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    directory = Path(output_root) / f"{repo_name}-{stamp}"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


job_store = ReviewJobStore()


async def launch_review_job(job_id: str) -> None:
    job = job_store.get(job_id)
    job.status = "running"
    job.updated_at = datetime.now(timezone.utc)

    async def callback(event: ProgressEvent) -> None:
        job_store.append_event(job_id, event)
        job.updated_at = datetime.now(timezone.utc)

    try:
        result = await run_review(job.request, progress_callback=callback)
        job.result = result
        job.status = "completed"
    except Exception as exc:  # pragma: no cover
        job.error = str(exc)
        job.status = "failed"
    finally:
        job.updated_at = datetime.now(timezone.utc)


def create_review_job(request: ReviewRequest) -> ReviewJob:
    job = job_store.create(request)
    return job
