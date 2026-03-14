from __future__ import annotations

import asyncio

import typer

from .config import DEFAULT_OUTPUT_ROOT
from .models import ReviewRequest
from .provider import env_hint_for_provider, resolve_model
from .service import run_review


app = typer.Typer(help="RepoReviewer CLI")


@app.callback()
def main() -> None:
    """RepoReviewer CLI."""


@app.command()
def review(
    github_url: str,
    pr: int | None = typer.Option(None, "--pr", help="Pull request number to review."),
    provider: str = typer.Option("openai", "--provider", help="LLM provider."),
    model: str | None = typer.Option(None, "--model", help="Model name or provider/model."),
    max_files: int = typer.Option(30, "--max-files", help="Max files to review."),
    max_file_bytes: int = typer.Option(40_000, "--max-file-bytes", help="Max bytes per file."),
    include_tests: bool = typer.Option(True, "--include-tests/--skip-tests", help="Include test files."),
    output_root: str = typer.Option(str(DEFAULT_OUTPUT_ROOT), "--output-root", help="Directory for review artifacts."),
) -> None:
    resolved_model = resolve_model(provider, model)
    typer.echo(f"Using {resolved_model}. Set {env_hint_for_provider(provider)} before running if required.")
    request = ReviewRequest(
        github_url=github_url,
        pr_number=pr,
        provider=provider,  # type: ignore[arg-type]
        model=resolved_model,
        max_files=max_files,
        max_file_bytes=max_file_bytes,
        include_tests=include_tests,
        output_root=output_root,
    )

    async def callback(event) -> None:
        typer.echo(f"[{event.percent:>3}%] {event.stage}: {event.message}")

    result = asyncio.run(run_review(request, progress_callback=callback))
    typer.echo(f"Review complete: {len(result.comments)} findings")
    typer.echo(f"JSON: {result.artifacts.json_path}")
    typer.echo(f"Markdown: {result.artifacts.markdown_path}")
