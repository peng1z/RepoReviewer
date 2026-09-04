from __future__ import annotations

import json
from pathlib import Path

import pytest

from repo_reviewer import service
from repo_reviewer.models import ProgressEvent, ReviewRequest
from repo_reviewer.service import ReviewJobStore, create_review_job, launch_review_job, run_review


def _request(tmp_path: Path, **overrides) -> ReviewRequest:
    base = {"github_url": "https://github.com/o/demo", "provider": "openai",
            "model": "openai/x", "output_root": str(tmp_path / "out")}
    base.update(overrides)
    return ReviewRequest(**base)


def _script(fake_llm) -> None:
    fake_llm.on_context({"readme_summary": "r", "folder_summary": "f", "architecture_notes": []})
    fake_llm.on_review([{"file": "pkg/core.py", "line": 4, "severity": "high",
                         "issue": "boundary", "suggestion": "use <"}])
    fake_llm.on_summary({"headline": "One high finding.", "top_findings": ["boundary"]})


# --- ReviewJobStore --------------------------------------------------------


def test_job_store_round_trip(tmp_path) -> None:
    store = ReviewJobStore()
    job = store.create(_request(tmp_path))
    assert store.get(job.id) is job
    assert job.status == "queued"


def test_job_store_records_events_per_job(tmp_path) -> None:
    store = ReviewJobStore()
    a, b = store.create(_request(tmp_path)), store.create(_request(tmp_path))
    store.append_event(a.id, ProgressEvent(stage="x", message="m", percent=1))
    assert len(store.list_events(a.id)) == 1
    assert store.list_events(b.id) == []


def test_unknown_job_raises() -> None:
    with pytest.raises(KeyError):
        ReviewJobStore().get("nope")


# --- run_review end to end -------------------------------------------------


@pytest.mark.asyncio
async def test_run_review_walks_every_agent_and_writes_artifacts(local_clone, fake_llm, tmp_path) -> None:
    _script(fake_llm)
    events: list[ProgressEvent] = []

    async def collect(event: ProgressEvent) -> None:
        events.append(event)

    result = await run_review(_request(tmp_path), progress_callback=collect)

    assert result.repo_name == "demo"
    assert [c.issue for c in result.comments] == ["boundary"]
    assert result.summary.headline == "One high finding."
    # review_agent emits one event per file, so collapse repeats to check order.
    ordered_unique = list(dict.fromkeys(e.stage for e in events))
    assert ordered_unique == ["cloner", "context", "review", "priority", "summary", "done"]
    reviewed = [e for e in events if e.stage == "review"]
    assert len(reviewed) == len(fake_llm.calls) - 2  # minus the context and summary calls
    assert all(e.message.startswith("Reviewed ") for e in reviewed)
    assert events[-1].percent == 100

    written = json.loads(Path(result.artifacts.json_path).read_text(encoding="utf-8"))
    assert written["repo_name"] == "demo"
    assert "# RepoReviewer Report: demo" in Path(result.artifacts.markdown_path).read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_run_review_output_dir_is_per_repo_and_timestamped(local_clone, fake_llm, tmp_path) -> None:
    _script(fake_llm)
    result = await run_review(_request(tmp_path))
    out = Path(result.artifacts.json_path).parent
    assert out.parent == tmp_path / "out"
    assert out.name.startswith("demo-")


@pytest.mark.asyncio
async def test_run_review_works_without_a_progress_callback(local_clone, fake_llm, tmp_path) -> None:
    _script(fake_llm)
    assert (await run_review(_request(tmp_path))).comments


# --- launch_review_job -----------------------------------------------------


@pytest.mark.asyncio
async def test_launch_marks_a_job_completed_with_its_result(local_clone, fake_llm, tmp_path) -> None:
    _script(fake_llm)
    job = create_review_job(_request(tmp_path))
    await launch_review_job(job.id)
    assert job.status == "completed"
    assert job.result is not None and job.result.repo_name == "demo"
    assert job.error is None
    assert service.job_store.list_events(job.id)[-1].stage == "done"


@pytest.mark.asyncio
async def test_launch_marks_a_job_failed_and_keeps_the_reason(monkeypatch, tmp_path) -> None:
    async def explode(request, *, progress_callback=None):
        raise RuntimeError("clone refused")

    monkeypatch.setattr("repo_reviewer.service.run_review", explode)
    job = create_review_job(_request(tmp_path))
    await launch_review_job(job.id)
    assert job.status == "failed"
    assert "clone refused" in job.error
    assert job.result is None
