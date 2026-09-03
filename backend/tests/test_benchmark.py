from pathlib import Path

import pytest
from pydantic import ValidationError

from repo_reviewer import benchmark as bench
from repo_reviewer.benchmark import (
    BenchmarkDataset,
    BenchmarkTarget,
    LINE_TOLERANCE,
    MutantOutcome,
    build_outcome,
    export_outcomes,
    export_summary,
    load_benchmark_dataset,
    plan_mutants,
    resolve_reviewable_files,
    run_benchmark_dataset,
    score_mutant,
    summarize_outcomes,
)
from repo_reviewer.models import ReviewComment
from repo_reviewer.mutation import Mutant


SAMPLE = '''def clamp(value, low, high):
    if value is None:
        return low
    if value <= low and high > 0:
        return low
    total = value + high
    return total
'''


def _mutant(line: int = 20, file: str = "pkg/core.py") -> Mutant:
    return Mutant(
        operator="off_by_one",
        file=file,
        line=line,
        original_line="if a <= b:",
        mutated_line="if a < b:",
        description="boundary weakened",
        keywords=("off-by-one", "boundary"),
    )


def _comment(file: str, line: int | None, issue: str = "something", suggestion: str = "fix it") -> ReviewComment:
    return ReviewComment(file=file, line=line, severity="medium", issue=issue, suggestion=suggestion)


# --- scoring -------------------------------------------------------------


def test_exact_line_match_is_a_hit() -> None:
    outcome, matched = score_mutant(_mutant(), [_comment("pkg/core.py", 20)])
    assert outcome == "hit"
    assert matched is not None


@pytest.mark.parametrize("offset", [-LINE_TOLERANCE, 0, LINE_TOLERANCE])
def test_line_within_tolerance_is_a_hit(offset: int) -> None:
    outcome, _ = score_mutant(_mutant(), [_comment("pkg/core.py", 20 + offset)])
    assert outcome == "hit"


@pytest.mark.parametrize("offset", [-(LINE_TOLERANCE + 1), LINE_TOLERANCE + 1])
def test_line_outside_tolerance_is_not_a_hit(offset: int) -> None:
    outcome, _ = score_mutant(_mutant(), [_comment("pkg/core.py", 20 + offset)])
    assert outcome == "miss"


def test_keyword_match_without_line_is_a_weak_hit() -> None:
    comment = _comment("pkg/core.py", None, issue="Possible off-by-one in the bound")
    outcome, matched = score_mutant(_mutant(), [comment])
    assert outcome == "weak_hit"
    assert matched is comment


def test_keyword_match_outside_tolerance_is_a_weak_hit() -> None:
    comment = _comment("pkg/core.py", 99, issue="boundary handling looks wrong")
    outcome, _ = score_mutant(_mutant(), [comment])
    assert outcome == "weak_hit"


def test_keyword_is_matched_in_suggestion_too() -> None:
    comment = _comment("pkg/core.py", None, issue="odd", suggestion="Check the BOUNDARY condition")
    assert score_mutant(_mutant(), [comment])[0] == "weak_hit"


def test_right_keyword_in_wrong_file_is_a_miss() -> None:
    comment = _comment("pkg/other.py", 20, issue="off-by-one here")
    assert score_mutant(_mutant(), [comment])[0] == "miss"


def test_no_findings_is_a_miss() -> None:
    assert score_mutant(_mutant(), []) == ("miss", None)


def test_positional_hit_wins_even_when_listed_after_a_weak_match() -> None:
    weak = _comment("pkg/core.py", None, issue="off-by-one somewhere")
    exact = _comment("pkg/core.py", 20, issue="unclear naming")
    outcome, matched = score_mutant(_mutant(), [weak, exact])
    assert outcome == "hit"
    assert matched is exact


@pytest.mark.parametrize("reported", ["pkg/core.py", "./pkg/core.py", "pkg\\core.py"])
def test_path_shapes_are_normalized(reported: str) -> None:
    assert score_mutant(_mutant(), [_comment(reported, 20)])[0] == "hit"


# --- aggregation ---------------------------------------------------------


def test_build_outcome_counts_findings_in_file() -> None:
    comments = [
        _comment("pkg/core.py", 20),
        _comment("pkg/core.py", 80),
        _comment("pkg/elsewhere.py", 5),
    ]
    outcome = build_outcome(_mutant(), comments, repo_name="demo", method="full")
    assert outcome.outcome == "hit"
    assert outcome.total_findings == 3
    assert outcome.findings_in_file == 2


def test_summarize_outcomes_computes_rates() -> None:
    rows = [
        MutantOutcome(repo_name="r", method="full", operator="off_by_one", file="a.py", line=1,
                      outcome="hit", total_findings=4),
        MutantOutcome(repo_name="r", method="full", operator="off_by_one", file="a.py", line=2,
                      outcome="weak_hit", total_findings=2),
        MutantOutcome(repo_name="r", method="full", operator="off_by_one", file="a.py", line=3,
                      outcome="miss", total_findings=0),
        MutantOutcome(repo_name="r", method="no_context", operator="off_by_one", file="a.py", line=1,
                      outcome="miss", total_findings=1),
    ]
    full, no_context = summarize_outcomes(rows)
    assert full.method == "full"
    assert (full.hits, full.weak_hits, full.misses) == (1, 1, 1)
    assert full.detection_rate == pytest.approx(1 / 3, abs=1e-4)
    assert full.weak_or_better_rate == pytest.approx(2 / 3, abs=1e-4)
    assert full.findings_per_mutant == pytest.approx(2.0)
    assert no_context.detection_rate == 0.0


def test_summarize_outcomes_handles_empty_input() -> None:
    assert summarize_outcomes([]) == []


def test_exports_write_expected_files(tmp_path: Path) -> None:
    rows = [MutantOutcome(repo_name="r", method="full", operator="off_by_one",
                          file="a.py", line=1, outcome="hit", total_findings=1)]
    outcomes_csv = tmp_path / "mutant-outcomes.csv"
    export_outcomes(rows, outcomes_csv)
    assert "off_by_one" in outcomes_csv.read_text(encoding="utf-8")

    csv_path, json_path, tex_path = export_summary(summarize_outcomes(rows), tmp_path)
    assert csv_path.exists() and json_path.exists()
    assert "\\begin{tabular}" in tex_path.read_text(encoding="utf-8")


# --- planning ------------------------------------------------------------


def _write_repo(root: Path) -> None:
    (root / "pkg").mkdir(parents=True, exist_ok=True)
    (root / "pkg" / "core.py").write_text(SAMPLE, encoding="utf-8")
    (root / "README.md").write_text("# demo\n", encoding="utf-8")


def test_resolve_reviewable_files_respects_max_files(tmp_path: Path) -> None:
    _write_repo(tmp_path)
    for index in range(10):
        (tmp_path / f"mod{index}.py").write_text(SAMPLE, encoding="utf-8")
    files = resolve_reviewable_files(tmp_path, include_tests=False, max_file_bytes=40_000, max_files=4)
    assert len(files) == 4


def test_resolve_reviewable_files_excludes_tests_when_asked(tmp_path: Path) -> None:
    _write_repo(tmp_path)
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_core.py").write_text(SAMPLE, encoding="utf-8")
    files = resolve_reviewable_files(tmp_path, include_tests=False, max_file_bytes=40_000, max_files=30)
    assert not any(path.startswith("tests/") for path in files)


def test_plan_mutants_only_targets_reviewable_files(tmp_path: Path) -> None:
    """A defect in an unreviewed file would be an automatic miss, so never inject one."""
    _write_repo(tmp_path)
    (tmp_path / "zzz_unreviewed.py").write_text(SAMPLE, encoding="utf-8")
    dataset = BenchmarkDataset(name="d", mutants_per_repo=5,
                               targets=[BenchmarkTarget(github_url="https://github.com/o/r")])
    target = BenchmarkTarget(github_url="https://github.com/o/r", max_files=2)
    reviewable = set(resolve_reviewable_files(tmp_path, include_tests=False,
                                              max_file_bytes=40_000, max_files=2))
    mutants = plan_mutants(tmp_path, target, dataset)
    assert mutants
    assert {m.file for m in mutants} <= reviewable


def test_load_benchmark_dataset(tmp_path: Path) -> None:
    path = tmp_path / "dataset.json"
    path.write_text(
        '{"name":"b","targets":[{"github_url":"https://github.com/pallets/click"}]}',
        encoding="utf-8",
    )
    dataset = load_benchmark_dataset(path)
    assert dataset.name == "b"
    assert dataset.mutants_per_repo == 10
    assert len(dataset.operators) == 5


# --- orchestration (offline) ---------------------------------------------


@pytest.mark.asyncio
async def test_run_benchmark_dataset_end_to_end(tmp_path: Path, monkeypatch) -> None:
    """Full orchestration with the review step stubbed -- no network, no API key."""
    source_repo = tmp_path / "source"
    source_repo.mkdir()
    _write_repo(source_repo)

    def fake_clone(url: str, destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            return destination
        import shutil
        shutil.copytree(source_repo, destination)
        return destination

    monkeypatch.setattr(bench, "clone_repo", fake_clone)

    seen: list[tuple[str, str]] = []

    async def fake_runner(*, repo_root, repo_name, method, dataset, target):
        seen.append((repo_name, method))
        # Report a finding on the mutated line of every reviewable Python file.
        return [_comment("pkg/core.py", 4, issue="boundary looks wrong")]

    dataset = BenchmarkDataset(
        name="offline",
        methods=["full", "single_agent"],
        mutants_per_repo=2,
        targets=[BenchmarkTarget(github_url="https://github.com/pallets/click")],
    )

    experiment_dir, outcomes = await run_benchmark_dataset(
        dataset,
        output_root=tmp_path / "out",
        workspace_root=tmp_path / "work",
        review_runner=fake_runner,
    )

    assert len(outcomes) == 4  # 2 mutants x 2 methods
    assert {method for _, method in seen} == {"full", "single_agent"}
    assert (experiment_dir / "dataset.json").exists()
    assert (experiment_dir / "mutant-outcomes.csv").exists()
    assert (experiment_dir / "benchmark-summary.csv").exists()
    assert not (experiment_dir / "unreachable.json").exists()


@pytest.mark.asyncio
async def test_repo_without_valid_mutants_is_recorded_as_unreachable(tmp_path: Path, monkeypatch) -> None:
    empty_repo = tmp_path / "empty"
    empty_repo.mkdir()
    (empty_repo / "README.md").write_text("# nothing to mutate\n", encoding="utf-8")

    def fake_clone(url: str, destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            import shutil
            shutil.copytree(empty_repo, destination)
        return destination

    monkeypatch.setattr(bench, "clone_repo", fake_clone)

    async def unused_runner(**kwargs):  # pragma: no cover - must never run
        raise AssertionError("review should not run when there is nothing to mutate")

    dataset = BenchmarkDataset(
        name="unreachable",
        targets=[BenchmarkTarget(github_url="https://github.com/pallets/click")],
    )
    experiment_dir, outcomes = await run_benchmark_dataset(
        dataset,
        output_root=tmp_path / "out",
        workspace_root=tmp_path / "work",
        review_runner=unused_runner,
    )
    assert outcomes == []
    assert (experiment_dir / "unreachable.json").exists()


# --- regressions ---------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [".github/workflows/ci.yml", ".env", ".config/settings.py"],
)
def test_dotfile_paths_are_not_mangled_by_normalization(path: str) -> None:
    """`lstrip("./")` strips characters, not a prefix, and would eat the dot."""
    mutant = _mutant(line=20, file=path)
    outcome, _ = score_mutant(mutant, [_comment(path, 20)])
    assert outcome == "hit"


def test_dotfile_does_not_match_its_dotless_twin() -> None:
    mutant = _mutant(line=20, file=".github/ci.py")
    assert score_mutant(mutant, [_comment("github/ci.py", 20)])[0] == "miss"


def test_parent_relative_path_is_left_alone() -> None:
    assert score_mutant(_mutant(file="../outside.py"), [_comment("outside.py", 20)])[0] == "miss"


@pytest.mark.asyncio
async def test_same_repo_name_under_different_owners_stays_separate(tmp_path: Path, monkeypatch) -> None:
    """org1/foo and org2/foo must not share a workspace or collapse in results."""
    source_repo = tmp_path / "source"
    source_repo.mkdir()
    _write_repo(source_repo)

    cloned_to: list[str] = []

    def fake_clone(url: str, destination: Path) -> Path:
        cloned_to.append(destination.name)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            import shutil
            shutil.copytree(source_repo, destination)
        return destination

    monkeypatch.setattr(bench, "clone_repo", fake_clone)

    async def fake_runner(*, repo_root, repo_name, method, dataset, target):
        return []

    dataset = BenchmarkDataset(
        name="collision",
        methods=["full"],
        mutants_per_repo=1,
        targets=[
            BenchmarkTarget(github_url="https://github.com/org1/foo"),
            BenchmarkTarget(github_url="https://github.com/org2/foo"),
        ],
    )
    _, outcomes = await run_benchmark_dataset(
        dataset,
        output_root=tmp_path / "out",
        workspace_root=tmp_path / "work",
        review_runner=fake_runner,
    )

    assert {o.repo_name for o in outcomes} == {"org1/foo", "org2/foo"}
    assert len(set(cloned_to)) == 2, f"workspaces collided: {cloned_to}"


def test_dataset_rejects_an_unknown_provider(tmp_path: Path) -> None:
    """Bad providers should fail at load, not after cloning and mutating."""
    path = tmp_path / "dataset.json"
    path.write_text(
        '{"name":"b","provider":"not-a-provider",'
        '"targets":[{"github_url":"https://github.com/pallets/click"}]}',
        encoding="utf-8",
    )
    with pytest.raises(ValidationError):
        load_benchmark_dataset(path)


@pytest.mark.asyncio
async def test_clone_runs_off_the_event_loop(tmp_path: Path, monkeypatch) -> None:
    """A blocking clone must not stall other coroutines.

    fake_clone waits for an event that only a concurrent coroutine can set. If
    the clone ran on the event loop that coroutine could never be scheduled, the
    wait would time out, and `released_in_time` would record False.
    """
    import asyncio
    import shutil
    import threading

    source_repo = tmp_path / "source"
    source_repo.mkdir()
    _write_repo(source_repo)

    gate = threading.Event()
    released_in_time: list[bool] = []

    def fake_clone(url: str, destination: Path) -> Path:
        released_in_time.append(gate.wait(timeout=5))
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            shutil.copytree(source_repo, destination)
        return destination

    monkeypatch.setattr(bench, "clone_repo", fake_clone)

    async def release_soon() -> None:
        await asyncio.sleep(0.05)
        gate.set()

    async def fake_runner(*, repo_root, repo_name, method, dataset, target):
        return []

    dataset = BenchmarkDataset(
        name="nonblocking",
        methods=["full"],
        mutants_per_repo=1,
        targets=[BenchmarkTarget(github_url="https://github.com/pallets/click")],
    )

    releaser = asyncio.create_task(release_soon())
    await asyncio.wait_for(
        run_benchmark_dataset(
            dataset,
            output_root=tmp_path / "out",
            workspace_root=tmp_path / "work",
            review_runner=fake_runner,
        ),
        timeout=15,
    )
    await releaser

    assert released_in_time == [True]
