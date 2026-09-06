from __future__ import annotations

from pathlib import Path

import pytest

from repo_reviewer.models import ProgressEvent, ProjectContext, ReviewComment, ReviewRequest, ReviewState
from repo_reviewer.workflow import (
    _bounded_content,
    _coerce_string,
    _coerce_string_list,
    cloner_agent,
    context_agent,
    priority_agent,
    review_agent,
    summary_agent,
)


def _request(**overrides) -> ReviewRequest:
    base = {"github_url": "https://github.com/o/demo", "provider": "openai", "model": "openai/x"}
    base.update(overrides)
    return ReviewRequest(**base)


def _state(repo: Path, **request_overrides) -> ReviewState:
    return ReviewState(request=_request(**request_overrides), workspace_dir=str(repo), repo_name="demo")


CONTEXT_JSON = {
    "readme_summary": "A demo.",
    "folder_summary": "pkg and tests.",
    "architecture_notes": ["single package"],
}


# --- coercion helpers ------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [(["a", 1], ["a", "1"]), ("solo", ["solo"]), (None, ["fb"]), (42, ["fb"]), ({}, ["fb"])],
)
def test_string_list_coercion(value, expected) -> None:
    assert _coerce_string_list(value, fallback=["fb"]) == expected


def test_string_list_without_fallback_is_empty() -> None:
    assert _coerce_string_list(None) == []


@pytest.mark.parametrize(
    ("value", "expected"),
    [("text", "text"), (None, "fb"), (["a"], '["a"]'), ({"k": 1}, '{"k": 1}'), (7, "7")],
)
def test_string_coercion(value, expected) -> None:
    assert _coerce_string(value, fallback="fb") == expected


# --- context_agent ---------------------------------------------------------


@pytest.mark.asyncio
async def test_context_builds_project_context_from_the_model(sample_repo, fake_llm) -> None:
    fake_llm.on_context(CONTEXT_JSON)
    state = await context_agent(_state(sample_repo))
    assert state.project_context == ProjectContext(
        readme_summary="A demo.",
        folder_summary="pkg and tests.",
        key_files=state.project_context.key_files,
        architecture_notes=["single package"],
    )
    assert "README.md" in state.project_context.key_files


@pytest.mark.asyncio
async def test_context_selects_source_files_and_skips_binaries(sample_repo, fake_llm) -> None:
    fake_llm.on_context(CONTEXT_JSON)
    state = await context_agent(_state(sample_repo))
    assert "pkg/core.py" in state.files_to_review
    assert not any(f.endswith(".png") for f in state.files_to_review)
    skipped = {item.path: item.reason for item in state.skipped_files}
    assert "logo.png" in skipped


@pytest.mark.asyncio
async def test_context_honours_max_files(sample_repo, fake_llm) -> None:
    fake_llm.on_context(CONTEXT_JSON)
    state = await context_agent(_state(sample_repo, max_files=2))
    assert len(state.files_to_review) == 2


@pytest.mark.asyncio
async def test_context_can_exclude_tests(sample_repo, fake_llm) -> None:
    fake_llm.on_context(CONTEXT_JSON)
    state = await context_agent(_state(sample_repo, include_tests=False))
    assert not any(f.startswith("tests/") for f in state.files_to_review)
    assert any(item.path.startswith("tests/") and item.reason == "test file" for item in state.skipped_files)


@pytest.mark.asyncio
async def test_context_sends_readme_and_key_files_to_the_model(sample_repo, fake_llm) -> None:
    fake_llm.on_context(CONTEXT_JSON)
    await context_agent(_state(sample_repo))
    prompt = fake_llm.calls[0]["user"]
    assert "A demo project." in prompt
    assert "FILE: pyproject.toml" in prompt


@pytest.mark.asyncio
async def test_context_survives_a_non_json_model_reply(sample_repo, fake_llm) -> None:
    """The review must not fail because the context model rambled."""
    fake_llm.on_context("I'm sorry, I cannot summarise that.")
    state = await context_agent(_state(sample_repo))
    assert state.project_context is not None
    assert "unavailable" in state.project_context.readme_summary.lower()
    assert state.files_to_review  # file selection is independent of the model


@pytest.mark.asyncio
async def test_context_coerces_a_string_note_into_a_list(sample_repo, fake_llm) -> None:
    fake_llm.on_context({**CONTEXT_JSON, "architecture_notes": "just one note"})
    state = await context_agent(_state(sample_repo))
    assert state.project_context.architecture_notes == ["just one note"]


@pytest.mark.asyncio
async def test_context_reports_progress(sample_repo, fake_llm) -> None:
    fake_llm.on_context(CONTEXT_JSON)
    events: list[ProgressEvent] = []

    async def collect(event: ProgressEvent) -> None:
        events.append(event)

    await context_agent(_state(sample_repo), collect)
    assert events[-1].stage == "context"
    assert events[-1].percent == 35


@pytest.mark.asyncio
async def test_context_pr_mode_reviews_only_changed_files(sample_repo, fake_llm, monkeypatch) -> None:
    fake_llm.on_context(CONTEXT_JSON)
    monkeypatch.setattr(
        "repo_reviewer.workflow.fetch_pr_changed_files", lambda url, pr, token: ["pkg/util.py"]
    )
    state = await context_agent(_state(sample_repo, pr_number=7))
    assert state.files_to_review == ["pkg/util.py"]


# --- review_agent ----------------------------------------------------------


def _prepared(sample_repo: Path, files: list[str]) -> ReviewState:
    state = _state(sample_repo)
    state.files_to_review = files
    state.project_context = ProjectContext(readme_summary="r", folder_summary="f")
    return state


@pytest.mark.asyncio
async def test_review_parses_findings_and_attaches_snippets(sample_repo, fake_llm) -> None:
    fake_llm.on_review([
        {"file": "pkg/core.py", "line": 4, "severity": "high", "issue": "boundary", "suggestion": "use <"}
    ])
    state = await review_agent(_prepared(sample_repo, ["pkg/core.py"]))
    assert len(state.comments) == 1
    comment = state.comments[0]
    assert comment.line == 4
    assert "value <= low" in comment.snippet


@pytest.mark.asyncio
async def test_review_sends_one_request_per_file_with_its_content(sample_repo, fake_llm) -> None:
    fake_llm.on_review([])
    await review_agent(_prepared(sample_repo, ["pkg/core.py", "pkg/util.py"]))
    assert len(fake_llm.calls) == 2
    assert "def clamp" in fake_llm.calls[0]["user"]
    assert "def helper" in fake_llm.calls[1]["user"]


@pytest.mark.asyncio
async def test_review_accepts_findings_wrapped_in_an_object(sample_repo, fake_llm) -> None:
    fake_llm.on_review({"comments": [
        {"file": "pkg/core.py", "line": 2, "severity": "low", "issue": "x", "suggestion": "y"}
    ]})
    state = await review_agent(_prepared(sample_repo, ["pkg/core.py"]))
    assert len(state.comments) == 1


@pytest.mark.asyncio
async def test_review_clears_a_line_number_past_the_end_of_the_file(sample_repo, fake_llm) -> None:
    # A real review of psf/requests put findings on lines 62, 66 and 69 of a
    # 51-line file. The finding is kept -- one of those three was a valid point
    # -- but the position it cites is not.
    line_count = len((sample_repo / "pkg" / "core.py").read_text().splitlines())
    fake_llm.on_review([
        {"file": "pkg/core.py", "line": line_count + 40, "severity": "low",
         "issue": "assert can be disabled with -O", "suggestion": "raise instead"},
        {"file": "pkg/core.py", "line": 2, "severity": "low", "issue": "in range", "suggestion": "s"},
    ])

    events: list[ProgressEvent] = []

    async def progress(event: ProgressEvent) -> None:
        events.append(event)

    state = await review_agent(_prepared(sample_repo, ["pkg/core.py"]), progress)

    by_issue = {comment.issue: comment for comment in state.comments}
    assert by_issue["assert can be disabled with -O"].line is None
    assert by_issue["in range"].line == 2
    assert any("outside the file cleared" in event.message for event in events)


@pytest.mark.asyncio
async def test_review_fills_in_a_missing_file_from_the_one_being_reviewed(sample_repo, fake_llm) -> None:
    fake_llm.on_review([{"line": 3, "severity": "medium", "issue": "x", "suggestion": "y"}])
    state = await review_agent(_prepared(sample_repo, ["pkg/core.py"]))
    assert state.comments[0].file == "pkg/core.py"


@pytest.mark.asyncio
async def test_review_skips_a_malformed_finding_and_says_so(sample_repo, fake_llm) -> None:
    fake_llm.on_review([
        {"file": "pkg/core.py", "line": 1, "severity": "high", "issue": "ok", "suggestion": "s"},
        {"file": "pkg/core.py", "severity": "catastrophic", "issue": "bad", "suggestion": "s"},
    ])
    events: list[ProgressEvent] = []

    async def collect(event: ProgressEvent) -> None:
        events.append(event)

    state = await review_agent(_prepared(sample_repo, ["pkg/core.py"]), collect)
    assert [c.issue for c in state.comments] == ["ok"]
    assert "1 malformed" in events[-1].message


@pytest.mark.asyncio
async def test_review_treats_non_json_as_no_findings(sample_repo, fake_llm) -> None:
    fake_llm.on_review("Looks fine to me!")
    state = await review_agent(_prepared(sample_repo, ["pkg/core.py"]))
    assert state.comments == []


@pytest.mark.asyncio
async def test_review_progress_never_exceeds_75_percent(sample_repo, fake_llm) -> None:
    fake_llm.on_review([])
    events: list[ProgressEvent] = []

    async def collect(event: ProgressEvent) -> None:
        events.append(event)

    await review_agent(_prepared(sample_repo, ["pkg/core.py", "pkg/util.py"]), collect)
    assert all(e.percent <= 75 for e in events)
    assert events[-1].percent == 75


# --- priority_agent --------------------------------------------------------


@pytest.mark.asyncio
async def test_priority_dedupes_and_orders_by_severity(sample_repo) -> None:
    state = _prepared(sample_repo, [])
    low = ReviewComment(file="a.py", line=1, severity="low", issue="dup", suggestion="s")
    state.comments = [
        low,
        ReviewComment(file="a.py", line=1, severity="low", issue="DUP ", suggestion="s"),
        ReviewComment(file="a.py", line=9, severity="high", issue="urgent", suggestion="s"),
    ]
    state = await priority_agent(state)
    assert [c.severity for c in state.comments] == ["high", "low"]


# --- summary_agent ---------------------------------------------------------


@pytest.mark.asyncio
async def test_summary_builds_from_the_model_reply(sample_repo, fake_llm) -> None:
    fake_llm.on_summary({"headline": "Mostly fine.", "top_findings": ["a", "b"]})
    state = _prepared(sample_repo, [])
    state = await summary_agent(state)
    assert state.summary.headline == "Mostly fine."
    assert state.summary.top_findings == ["a", "b"]
    assert state.summary.skipped_notes


@pytest.mark.asyncio
async def test_summary_caps_top_findings_at_five(sample_repo, fake_llm) -> None:
    fake_llm.on_summary({"headline": "h", "top_findings": list("abcdefgh")})
    state = await summary_agent(_prepared(sample_repo, []))
    assert len(state.summary.top_findings) == 5


@pytest.mark.asyncio
async def test_summary_falls_back_to_the_findings_when_the_model_is_not_json(sample_repo, fake_llm) -> None:
    fake_llm.on_summary("no json here")
    state = _prepared(sample_repo, [])
    state.comments = [ReviewComment(file="a.py", line=1, severity="high", issue="leak", suggestion="s")]
    state = await summary_agent(state)
    assert "not valid JSON" in state.summary.headline
    assert state.summary.top_findings == ["High: leak"]


@pytest.mark.asyncio
async def test_summary_fallback_with_no_findings_says_so(sample_repo, fake_llm) -> None:
    fake_llm.on_summary("")
    state = await summary_agent(_prepared(sample_repo, []))
    assert state.summary.top_findings == ["No actionable findings were returned."]


@pytest.mark.asyncio
async def test_summary_sends_findings_and_skipped_files_to_the_model(sample_repo, fake_llm) -> None:
    fake_llm.on_summary({"headline": "h", "top_findings": []})
    state = _prepared(sample_repo, [])
    state.comments = [ReviewComment(file="a.py", line=1, severity="low", issue="the-issue", suggestion="s")]
    await summary_agent(state)
    assert "the-issue" in fake_llm.calls[0]["user"]


# --- cloner_agent ----------------------------------------------------------


@pytest.mark.asyncio
async def test_cloner_records_repo_name_and_workspace(monkeypatch, tmp_path) -> None:
    cloned: list[tuple[str, Path]] = []

    def fake_clone(url: str, destination: Path) -> Path:
        cloned.append((url, destination))
        return destination

    monkeypatch.setattr("repo_reviewer.workflow.clone_repo", fake_clone)
    monkeypatch.chdir(tmp_path)
    state = await cloner_agent(ReviewState(request=_request()))
    assert state.repo_name == "demo"
    assert state.workspace_dir.endswith("demo")
    assert cloned[0][0] == "https://github.com/o/demo"


@pytest.mark.asyncio
async def test_cloner_checks_out_the_pr_head_in_pr_mode(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("repo_reviewer.workflow.clone_repo", lambda url, dest: dest)
    seen: dict = {}
    monkeypatch.setattr(
        "repo_reviewer.workflow.checkout_pr_head",
        lambda url, dest, pr, token: seen.update({"pr": pr}),
    )
    monkeypatch.chdir(tmp_path)
    await cloner_agent(ReviewState(request=_request(pr_number=42)))
    assert seen["pr"] == 42


@pytest.mark.asyncio
async def test_context_records_files_dropped_by_the_max_files_cap(local_clone, fake_llm) -> None:
    # A review of psf/requests reported 109 skipped files with reasons and said
    # nothing about the 40 eligible files it never opened, so the report read as
    # complete coverage of 17% of the repository.
    fake_llm.on_context(CONTEXT_JSON)
    state = _state(local_clone, max_files=1)
    state.workspace_dir = None

    state = await cloner_agent(state)
    state = await context_agent(state)

    assert len(state.files_to_review) == 1
    over_cap = [item for item in state.skipped_files if item.reason.startswith("not reviewed: max_files")]
    assert over_cap, "files cut by the cap must be reported, not silently dropped"
    assert "max_files limit of 1" in over_cap[0].reason
    # Every eligible file is accounted for: reviewed, or explained.
    assert set(state.files_to_review).isdisjoint(item.path for item in over_cap)


@pytest.mark.asyncio
async def test_summary_states_coverage_when_files_were_cut(local_clone, fake_llm) -> None:
    fake_llm.on_context(CONTEXT_JSON)
    fake_llm.on_review([])
    fake_llm.on_summary({"headline": "ok", "top_findings": ["f"]})
    state = _state(local_clone, max_files=1)
    state.workspace_dir = None

    state = await cloner_agent(state)
    state = await context_agent(state)
    state = await review_agent(state)
    state = await summary_agent(state)

    coverage = [note for note in state.summary.skipped_notes if note.startswith("Coverage:")]
    assert len(coverage) == 1
    assert "1 of" in coverage[0]
    assert "never opened" in coverage[0]


@pytest.mark.asyncio
async def test_summary_omits_the_coverage_note_when_nothing_was_cut(local_clone, fake_llm) -> None:
    fake_llm.on_context(CONTEXT_JSON)
    fake_llm.on_review([])
    fake_llm.on_summary({"headline": "ok", "top_findings": ["f"]})
    state = _state(local_clone, max_files=500)
    state.workspace_dir = None

    state = await cloner_agent(state)
    state = await context_agent(state)
    state = await review_agent(state)
    state = await summary_agent(state)

    assert not any(note.startswith("Coverage:") for note in state.summary.skipped_notes)


def test_bounded_content_uses_the_setting_that_governs_collection() -> None:
    # The review truncated at a hard-coded 12,000 characters while the
    # collector accepted up to max_file_bytes, so every file between the two
    # was reviewed on a prefix with nothing said about it.
    text = "x" * 20_000

    whole, truncated = _bounded_content(text, 40_000)
    assert whole == text
    assert truncated is None

    cut, truncated = _bounded_content(text, 5_000)
    assert len(cut) == 5_000
    assert truncated == 20_000


@pytest.mark.asyncio
async def test_review_tells_the_model_when_it_only_sent_part_of_a_file(sample_repo, fake_llm) -> None:
    # Four of eleven high-severity findings in a review of psf/requests were
    # the model describing this pipeline's own truncation as broken code:
    # "incomplete method", "truncated", "syntax error".
    big = sample_repo / "pkg" / "big.py"
    big.write_text("# pad\n" * 4000, encoding="utf-8")
    fake_llm.on_review([])
    state = _prepared(sample_repo, ["pkg/big.py"])
    state.request.max_file_bytes = 500

    state = await review_agent(state)

    sent = fake_llm.calls[0]["user"]
    assert "[TRUNCATED:" in sent
    assert "do not report the cut-off as a defect" in sent
    partial = [item for item in state.skipped_files if item.reason.startswith("partially reviewed")]
    assert len(partial) == 1
    assert partial[0].path == "pkg/big.py"


@pytest.mark.asyncio
async def test_review_says_nothing_about_truncation_when_it_sent_the_whole_file(
    sample_repo, fake_llm
) -> None:
    fake_llm.on_review([])
    state = await review_agent(_prepared(sample_repo, ["pkg/core.py"]))

    assert "[TRUNCATED:" not in fake_llm.calls[0]["user"]
    assert not any(item.reason.startswith("partially reviewed") for item in state.skipped_files)
