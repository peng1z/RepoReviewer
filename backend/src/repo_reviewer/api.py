from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from sse_starlette.sse import EventSourceResponse

from .models import ReviewRequest
from .service import create_review_job, job_store, launch_review_job


app = FastAPI(title="RepoReviewer API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/reviews")
async def create_review(request: ReviewRequest, background_tasks: BackgroundTasks):
    job = create_review_job(request)
    background_tasks.add_task(launch_review_job, job.id)
    return {"job_id": job.id, "status": job.status}


@app.get("/reviews/{job_id}")
async def get_review(job_id: str):
    try:
        return job_store.get(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Job not found") from exc


@app.get("/reviews/{job_id}/events")
async def stream_review_events(job_id: str):
    if job_id not in job_store.jobs:
        raise HTTPException(status_code=404, detail="Job not found")

    async def event_generator():
        seen = 0
        while True:
            events = job_store.list_events(job_id)
            while seen < len(events):
                payload = events[seen]
                seen += 1
                yield {"event": "progress", "data": payload.model_dump_json()}
            job = job_store.get(job_id)
            if job.status in {"completed", "failed"}:
                yield {"event": "status", "data": json.dumps({"status": job.status, "error": job.error})}
                break
            await asyncio.sleep(0.5)

    return EventSourceResponse(event_generator())


@app.get("/reviews/{job_id}/artifacts/{artifact_name}")
async def download_artifact(job_id: str, artifact_name: str):
    try:
        job = job_store.get(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Job not found") from exc
    if not job.result:
        raise HTTPException(status_code=404, detail="Artifacts not ready")
    lookup = {
        "review.json": job.result.artifacts.json_path,
        "review.md": job.result.artifacts.markdown_path,
    }
    if artifact_name not in lookup:
        raise HTTPException(status_code=404, detail="Artifact not found")
    path = Path(lookup[artifact_name])
    return FileResponse(path, filename=path.name, media_type="application/octet-stream")
