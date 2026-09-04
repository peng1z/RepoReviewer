from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from repo_reviewer import cli
from repo_reviewer.models import (
    ProjectContext, ReviewArtifacts, ReviewComment, ReviewResult, ReviewSummary,
)

runner = CliRunner()


def _result() -> ReviewResult:
    return ReviewResult(
        repo_url="https://github.com/o/demo", repo_name="demo", mode="repo",
        provider="openai", model="openai/x",
        context=ProjectContext(readme_summary="r", folder_summary="f"),
        comments=[ReviewComment(file="a.py", line=1, severity="low", issue="i", suggestion="s")],
        summary=ReviewSummary(headline="h", top_findings=[]),
        artifacts=ReviewArtifacts(json_path="/tmp/review.json", markdown_path="/tmp/review.md"),
    )


def test_help_lists_every_command() -> None:
    out = runner.invoke(cli.app, ["--help"]).output
    for name in ("review", "evaluate", "summarize-annotations", "benchmark"):
        assert name in out


def test_review_passes_flags_through_and_prints_artifacts(monkeypatch) -> None:
    seen: dict = {}

    async def fake_run_review(request, *, progress_callback=None):
        seen["request"] = request
        return _result()

    monkeypatch.setattr(cli, "run_review", fake_run_review)
    res = runner.invoke(cli.app, [
        "review", "https://github.com/o/demo", "--pr", "5", "--provider", "openrouter",
        "--model", "minimax/minimax-m2.7:free", "--max-files", "3", "--skip-tests",
    ])
    assert res.exit_code == 0, res.output
    req = seen["request"]
    assert req.pr_number == 5 and req.max_files == 3 and req.include_tests is False
    # A model that already contains "/" is taken as fully qualified and is NOT
    # prefixed with the provider -- so for OpenRouter the caller must write
    # "openrouter/minimax/..." explicitly. This test pins that contract.
    assert req.model == "minimax/minimax-m2.7:free"
    assert "1 findings" in res.output
    assert "/tmp/review.md" in res.output


def test_review_resolves_a_bare_model_name_against_the_provider(monkeypatch) -> None:
    seen: dict = {}

    async def fake_run_review(request, *, progress_callback=None):
        seen["model"] = request.model
        return _result()

    monkeypatch.setattr(cli, "run_review", fake_run_review)
    runner.invoke(cli.app, ["review", "https://github.com/o/demo", "--provider", "groq", "--model", "llama"])
    assert seen["model"] == "groq/llama"


def test_benchmark_configures_the_llm_policy_and_reports_hits(monkeypatch, tmp_path) -> None:
    policy: dict = {}
    monkeypatch.setattr(cli, "configure_llm", lambda **kw: policy.update(kw))

    from repo_reviewer.benchmark import MutantOutcome
    outcomes = [
        MutantOutcome(repo_name="r", method="full", operator="off_by_one", file="a.py", line=1, outcome="hit"),
        MutantOutcome(repo_name="r", method="full", operator="off_by_one", file="a.py", line=2, outcome="miss"),
    ]
    seen: dict = {}

    def fake_run(dataset, output_root, workspace_root, resume_from=None):
        seen.update(dataset=dataset, resume=resume_from)
        return tmp_path / "exp", outcomes

    monkeypatch.setattr(cli, "run_benchmark_sync", fake_run)
    res = runner.invoke(cli.app, ["benchmark", "ds.json", "--rpm", "18", "--max-retries", "4",
                                  "--request-timeout", "150", "--resume", "prev"])
    assert res.exit_code == 0, res.output
    assert policy == {"requests_per_minute": 18, "max_retries": 4, "request_timeout_seconds": 150.0}
    assert seen["resume"] == Path("prev")
    assert "2 scored" in res.output and "1 positional hits" in res.output


def test_evaluate_and_summarize_delegate(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(cli, "run_evaluation_sync", lambda d, o: (tmp_path / "exp", [1, 2, 3]))
    res = runner.invoke(cli.app, ["evaluate", "ds.json"])
    assert res.exit_code == 0 and "3 runs" in res.output

    monkeypatch.setattr(cli, "summarize_annotations_file", lambda s, o: (tmp_path / "a.csv", tmp_path / "a.json"))
    res = runner.invoke(cli.app, ["summarize-annotations", "sheet.csv"])
    assert res.exit_code == 0 and "a.csv" in res.output


def test_review_nags_about_the_key_only_when_it_is_missing(monkeypatch) -> None:
    async def fake_run_review(request, *, progress_callback=None):
        return _result()

    monkeypatch.setattr(cli, "run_review", fake_run_review)

    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    out = runner.invoke(cli.app, ["review", "https://github.com/o/demo", "--provider", "groq"]).output
    assert "Set GROQ_API_KEY before running." in out

    monkeypatch.setenv("GROQ_API_KEY", "sk-set")
    out = runner.invoke(cli.app, ["review", "https://github.com/o/demo", "--provider", "groq"]).output
    assert "GROQ_API_KEY" not in out
    assert "Using groq/" in out
