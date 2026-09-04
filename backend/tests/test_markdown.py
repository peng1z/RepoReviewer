from __future__ import annotations

from repo_reviewer.markdown import render_review_markdown
from repo_reviewer.models import (
    ProjectContext, ReviewArtifacts, ReviewComment, ReviewResult, ReviewSummary, SkippedFile,
)


def _result(comments=(), skipped=()) -> ReviewResult:
    return ReviewResult(
        repo_url="https://github.com/o/demo", repo_name="demo", mode="repo",
        provider="openai", model="openai/x",
        context=ProjectContext(readme_summary="README says hi", folder_summary="flat"),
        comments=list(comments), skipped_files=list(skipped),
        summary=ReviewSummary(headline="Looks OK", top_findings=["one", "two"]),
        artifacts=ReviewArtifacts(json_path="/j", markdown_path="/m"),
    )


def _c(severity, line=1, snippet=None) -> ReviewComment:
    return ReviewComment(file="a.py", line=line, severity=severity, issue=f"{severity} issue",
                         suggestion="fix", snippet=snippet)


def test_header_summary_and_context_are_rendered() -> None:
    md = render_review_markdown(_result())
    assert md.startswith("# RepoReviewer Report: demo")
    for fragment in ("- Mode: repo", "Looks OK", "- one", "- two", "README says hi", "- Structure: flat"):
        assert fragment in md


def test_findings_are_grouped_by_severity_in_fixed_order() -> None:
    md = render_review_markdown(_result([_c("low"), _c("high"), _c("medium")]))
    assert md.index("## High") < md.index("## Medium") < md.index("## Low")
    assert md.index("high issue") < md.index("medium issue") < md.index("low issue")


def test_empty_severity_sections_say_so() -> None:
    md = render_review_markdown(_result([_c("high")]))
    assert md.count("_No findings._") == 2


def test_location_includes_the_line_only_when_known() -> None:
    md = render_review_markdown(_result([_c("high", line=12), _c("low", line=None)]))
    assert "### a.py:12" in md
    assert "### a.py\n" in md


def test_snippet_is_fenced_when_present() -> None:
    md = render_review_markdown(_result([_c("high", snippet="1: x = 1")]))
    assert "```text\n1: x = 1\n```" in md


def test_skipped_files_are_listed_or_marked_none() -> None:
    assert "- None" in render_review_markdown(_result())
    md = render_review_markdown(_result(skipped=[SkippedFile(path="big.bin", reason="too large")]))
    assert "- `big.bin`: too large" in md


def test_output_ends_with_exactly_one_newline() -> None:
    md = render_review_markdown(_result())
    assert md.endswith("\n") and not md.endswith("\n\n")
