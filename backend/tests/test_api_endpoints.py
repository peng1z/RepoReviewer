from __future__ import annotations

from fastapi.testclient import TestClient

from repo_reviewer import api
from repo_reviewer.models import (
    ProgressEvent, ProjectContext, ReviewArtifacts, ReviewRequest, ReviewResult, ReviewSummary,
)
from repo_reviewer.service import job_store


def _client(raise_server_exceptions: bool = True) -> TestClient:
    return TestClient(api.app, raise_server_exceptions=raise_server_exceptions)


def _job(tmp_path, *, status="queued", with_result=False):
    job = job_store.create(ReviewRequest(github_url="https://github.com/o/demo"))
    job.status = status
    if with_result:
        json_path = tmp_path / "review.json"
        md_path = tmp_path / "review.md"
        json_path.write_text('{"ok": true}', encoding="utf-8")
        md_path.write_text("# report", encoding="utf-8")
        job.result = ReviewResult(
            repo_url="https://github.com/o/demo", repo_name="demo", mode="repo",
            provider="openai", model="openai/x",
            context=ProjectContext(readme_summary="r", folder_summary="f"),
            comments=[], summary=ReviewSummary(headline="h", top_findings=[]),
            artifacts=ReviewArtifacts(json_path=str(json_path), markdown_path=str(md_path)),
        )
    return job


def test_health() -> None:
    assert _client().get("/health").json() == {"status": "ok"}


def test_unknown_job_is_404() -> None:
    response = _client().get("/reviews/nope")
    assert response.status_code == 404
    assert response.json()["detail"] == "Job not found"


def test_job_can_be_fetched(tmp_path) -> None:
    job = _job(tmp_path)
    body = _client().get(f"/reviews/{job.id}").json()
    assert body["id"] == job.id and body["status"] == "queued"


def test_event_stream_replays_progress_then_final_status(tmp_path) -> None:
    job = _job(tmp_path, status="completed")
    job_store.append_event(job.id, ProgressEvent(stage="cloner", message="Cloned", percent=15))
    job_store.append_event(job.id, ProgressEvent(stage="done", message="Saved", percent=100))

    body = _client().get(f"/reviews/{job.id}/events").text
    assert body.count("event: progress") == 2
    assert '"stage": "cloner"' in body.replace('\\"', '"') or '"stage":"cloner"' in body
    assert body.count("event: status") == 1
    assert '"status": "completed"' in body


def test_event_stream_reports_a_failed_job(tmp_path) -> None:
    job = _job(tmp_path, status="failed")
    job.error = "clone refused"
    body = _client().get(f"/reviews/{job.id}/events").text
    assert '"status": "failed"' in body and "clone refused" in body


def test_event_stream_for_an_unknown_job_is_404() -> None:
    assert _client().get("/reviews/nope/events").status_code == 404


def test_artifacts_are_served_once_the_job_has_a_result(tmp_path) -> None:
    job = _job(tmp_path, status="completed", with_result=True)
    client = _client()
    assert client.get(f"/reviews/{job.id}/artifacts/review.json").text == '{"ok": true}'
    md = client.get(f"/reviews/{job.id}/artifacts/review.md")
    assert md.text == "# report"
    assert "review.md" in md.headers["content-disposition"]


def test_artifacts_before_completion_are_404(tmp_path) -> None:
    job = _job(tmp_path)
    response = _client().get(f"/reviews/{job.id}/artifacts/review.json")
    assert response.status_code == 404 and response.json()["detail"] == "Artifacts not ready"


def test_an_unknown_artifact_name_is_404(tmp_path) -> None:
    job = _job(tmp_path, status="completed", with_result=True)
    assert _client().get(f"/reviews/{job.id}/artifacts/secrets.txt").status_code == 404


def test_artifact_download_for_an_unknown_job_is_404_not_500() -> None:
    """get_review maps a missing job to 404; the artifact route must not differ."""
    response = _client(raise_server_exceptions=False).get("/reviews/nope/artifacts/review.json")
    assert response.status_code == 404, response.status_code


def test_cors_allows_the_frontend_origin() -> None:
    response = _client().options(
        "/health",
        headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "GET"},
    )
    assert response.headers.get("access-control-allow-origin") == "http://localhost:3000"
