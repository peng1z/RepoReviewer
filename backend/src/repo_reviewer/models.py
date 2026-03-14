from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, HttpUrl


Severity = Literal["high", "medium", "low"]
ReviewMode = Literal["repo", "pr"]
ProviderName = Literal["openai", "anthropic", "openrouter", "groq", "ollama", "custom"]
JobStatus = Literal["queued", "running", "completed", "failed"]
DEFAULT_OUTPUT_ROOT = str(Path(__file__).resolve().parents[3] / "outputs")


class ReviewComment(BaseModel):
    file: str
    line: int | None = None
    severity: Severity
    issue: str
    suggestion: str
    snippet: str | None = None


class SkippedFile(BaseModel):
    path: str
    reason: str


class ProjectContext(BaseModel):
    readme_summary: str
    folder_summary: str
    key_files: list[str] = Field(default_factory=list)
    architecture_notes: list[str] = Field(default_factory=list)


class ReviewSummary(BaseModel):
    headline: str
    top_findings: list[str]
    skipped_notes: list[str] = Field(default_factory=list)


class ReviewArtifacts(BaseModel):
    json_path: str
    markdown_path: str


class ReviewResult(BaseModel):
    repo_url: HttpUrl
    repo_name: str
    mode: ReviewMode
    provider: str
    model: str
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    context: ProjectContext
    comments: list[ReviewComment]
    skipped_files: list[SkippedFile] = Field(default_factory=list)
    summary: ReviewSummary
    artifacts: ReviewArtifacts


class ReviewRequest(BaseModel):
    github_url: HttpUrl
    pr_number: int | None = None
    provider: ProviderName = "openai"
    model: str = "gpt-4.1-mini"
    max_files: int = 30
    max_file_bytes: int = 40_000
    include_tests: bool = True
    output_root: str = DEFAULT_OUTPUT_ROOT

    @property
    def mode(self) -> ReviewMode:
        return "pr" if self.pr_number is not None else "repo"


class ProgressEvent(BaseModel):
    stage: str
    message: str
    percent: int = Field(ge=0, le=100)
    payload: dict[str, Any] | None = None


class ReviewState(BaseModel):
    request: ReviewRequest
    workspace_dir: str | None = None
    repo_name: str | None = None
    files_to_review: list[str] = Field(default_factory=list)
    skipped_files: list[SkippedFile] = Field(default_factory=list)
    project_context: ProjectContext | None = None
    comments: list[ReviewComment] = Field(default_factory=list)
    summary: ReviewSummary | None = None
    progress: list[ProgressEvent] = Field(default_factory=list)


class ReviewJob(BaseModel):
    id: str
    status: JobStatus = "queued"
    request: ReviewRequest
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    result: ReviewResult | None = None
    error: str | None = None


class ResolvedConfig(BaseModel):
    path: Path
    data: dict[str, Any]
