from __future__ import annotations

from fastapi.testclient import TestClient

from repo_reviewer.api import app


def test_create_review_job_can_be_polled(monkeypatch) -> None:
    from repo_reviewer.models import ProjectContext, ReviewArtifacts, ReviewResult, ReviewSummary

    async def fake_launch_review_job(job_id: str) -> None:
        from repo_reviewer.service import job_store

        job = job_store.get(job_id)
        job.status = "completed"
        job.result = ReviewResult(
            repo_url="https://github.com/octocat/Hello-World",
            repo_name="Hello-World",
            mode="repo",
            provider="groq",
            model="groq/llama-3.3-70b-versatile",
            context=ProjectContext(
                readme_summary="summary",
                folder_summary="folders",
                key_files=["README"],
                architecture_notes=["note"],
            ),
            comments=[],
            summary=ReviewSummary(headline="done", top_findings=["finding"]),
            artifacts=ReviewArtifacts(json_path="/tmp/review.json", markdown_path="/tmp/review.md"),
        )

    monkeypatch.setattr("repo_reviewer.api.launch_review_job", fake_launch_review_job)

    client = TestClient(app)
    response = client.post(
        "/reviews",
        json={
            "github_url": "https://github.com/octocat/Hello-World",
            "provider": "groq",
            "model": "groq/llama-3.3-70b-versatile",
        },
    )
    assert response.status_code == 200
    job_id = response.json()["job_id"]

    status_response = client.get(f"/reviews/{job_id}")
    assert status_response.status_code == 200
    assert status_response.json()["status"] == "completed"
