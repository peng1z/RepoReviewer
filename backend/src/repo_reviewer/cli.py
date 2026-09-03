from __future__ import annotations

import asyncio
from pathlib import Path

import typer

from .config import DEFAULT_OUTPUT_ROOT
from .annotation_analysis import summarize_annotations as summarize_annotations_file
from .benchmark import run_benchmark_sync
from .provider import configure_llm
from .evaluation import run_evaluation_sync
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


@app.command()
def evaluate(
    dataset: str = typer.Argument(..., help="Path to evaluation dataset JSON."),
    output_root: str = typer.Option("../paper/experiments", "--output-root", help="Directory for experiment outputs."),
) -> None:
    experiment_dir, rows = run_evaluation_sync(Path(dataset), Path(output_root))
    typer.echo(f"Evaluation complete: {len(rows)} runs")
    typer.echo(f"Artifacts: {experiment_dir}")


@app.command()
def summarize_annotations(
    annotation_sheet: str = typer.Argument(..., help="Path to annotation-sheet.csv."),
    output_root: str = typer.Option("../paper/experiments/summary", "--output-root", help="Directory for aggregated annotation outputs."),
) -> None:
    csv_path, json_path = summarize_annotations_file(Path(annotation_sheet), Path(output_root))
    typer.echo(f"Annotation summary CSV: {csv_path}")
    typer.echo(f"Annotation summary JSON: {json_path}")


@app.command()
def benchmark(
    dataset: str = typer.Argument(..., help="Path to mutation benchmark dataset JSON."),
    output_root: str = typer.Option("../paper/experiments", "--output-root", help="Directory for benchmark outputs."),
    workspace_root: str = typer.Option(".cache/repo-reviewer-benchmark", "--workspace-root", help="Scratch directory for cloned and mutated copies."),
    requests_per_minute: int | None = typer.Option(None, "--rpm", help="Cap LLM calls per minute (0 disables pacing)."),
    max_retries: int | None = typer.Option(None, "--max-retries", help="Retries for rate-limit and transient errors."),
    request_timeout: float | None = typer.Option(None, "--request-timeout", help="Seconds before a single LLM call is abandoned."),
) -> None:
    """Inject known defects into real repositories and measure detection rates."""
    configure_llm(
        requests_per_minute=requests_per_minute,
        max_retries=max_retries,
        request_timeout_seconds=request_timeout,
    )
    experiment_dir, outcomes = run_benchmark_sync(
        Path(dataset), Path(output_root), Path(workspace_root)
    )
    hits = sum(1 for outcome in outcomes if outcome.outcome == "hit")
    typer.echo(f"Benchmark complete: {len(outcomes)} scored mutant/method pairs, {hits} positional hits")
    typer.echo(f"Artifacts: {experiment_dir}")
