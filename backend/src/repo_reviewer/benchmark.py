from __future__ import annotations

import asyncio
import csv
import json
import shutil
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, Field

from .config import load_repo_config
from .evaluation import EvaluationMethod
from .github_client import clone_repo, parse_github_url
from .models import ProviderName, ReviewComment
from .mutation import ALL_OPERATORS, Mutant, MutationOperator, apply_mutant, select_mutants
from .repository import collect_repo_files, prioritize_files


Outcome = Literal["hit", "weak_hit", "miss"]

#: A finding must land within this many lines of the injected defect to count as
#: a positional hit. Reviewers routinely point at the enclosing statement rather
#: than the exact line, so a small window is necessary; a large one would start
#: crediting unrelated findings in the same function.
LINE_TOLERANCE = 3


class BenchmarkTarget(BaseModel):
    github_url: str
    label: str | None = None
    include_tests: bool = False
    max_files: int = 30
    max_file_bytes: int = 40_000
    #: Extra ignore patterns written into the clone as .repo-reviewer.toml.
    #: prioritize_files() orders same-depth sources alphabetically, so on click
    #: every slot went to examples/ and src/click was never reviewed. Reviewing
    #: sample scripts is not representative of reviewing library code.
    ignore_globs: list[str] = Field(default_factory=list)


class BenchmarkDataset(BaseModel):
    name: str
    # Constrained so an unusable provider fails at dataset load, rather than
    # after cloning and mutating a repository.
    provider: ProviderName = "openai"
    model: str = "openai/gpt-4.1-mini"
    methods: list[EvaluationMethod] = Field(
        default_factory=lambda: ["full", "single_agent", "no_context", "no_priority"]
    )
    operators: list[MutationOperator] = Field(default_factory=lambda: list(ALL_OPERATORS))
    mutants_per_repo: int = 10
    seed: int = 20260902
    targets: list[BenchmarkTarget]


class MutantOutcome(BaseModel):
    repo_name: str
    method: str
    operator: str
    file: str
    line: int
    outcome: Outcome
    matched_file: str | None = None
    matched_line: int | None = None
    matched_issue: str | None = None
    matched_suggestion: str | None = None
    matched_keywords: str = ""
    total_findings: int = 0
    findings_in_file: int = 0
    #: Lines in the mutated file. Needed to work out how often a finding
    #: would land in the tolerance window by chance alone.
    file_lines: int = 0


class BenchmarkError(BaseModel):
    """A mutant/method pair that could not be scored. Excluded from all rates."""

    repo_name: str
    method: str
    operator: str
    file: str
    line: int
    error: str


class MethodSummary(BaseModel):
    method: str
    scored_mutants: int
    hits: int
    weak_hits: int
    misses: int
    detection_rate: float
    weak_or_better_rate: float
    findings_per_mutant: float
    #: How often a method this verbose would land inside the tolerance window
    #: with no review skill at all.
    expected_by_chance: float = 0.0
    #: detection_rate / expected_by_chance. Reading detection_rate alone
    #: rewards verbosity: a method reporting 15 findings per file has roughly
    #: three times the chance of a lucky hit as one reporting 5.
    lift_over_chance: float = 0.0


class DatasetMismatch(RuntimeError):
    """The run being resumed was produced by a different dataset."""


def completed_units(experiment_dir: Path) -> set[tuple]:
    """Mutant/method pairs already scored in this experiment directory.

    Only successful outcomes count. Previously failed pairs are retried: a
    failure is usually a rate limit or a timeout, it is excluded from the rates
    anyway, and retrying it can only widen coverage.
    """
    units: set[tuple] = set()
    log = experiment_dir / "mutant-outcomes.jsonl"
    if not log.exists():
        return units
    for line in log.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        units.add(_unit_key(row["repo_name"], row["operator"], row["file"], row["line"], row["method"]))
    return units


def _unit_key(repo_name: str, operator: str, file: str, line: int, method: str) -> tuple:
    return (repo_name, operator, file, int(line), method)


def _load_jsonl(path: Path, model):
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            records.append(model.model_validate_json(line))
    return records


def _assert_same_dataset(experiment_dir: Path, dataset: BenchmarkDataset) -> None:
    """Refuse to append results produced under a different configuration.

    Mixing runs from two datasets would corrupt the metrics silently, which is
    far worse than repeating the work.
    """
    stored = experiment_dir / "dataset.json"
    if not stored.exists():
        raise DatasetMismatch(f"{experiment_dir} has no dataset.json to resume from")
    previous = BenchmarkDataset.model_validate_json(stored.read_text(encoding="utf-8"))
    if previous.model_dump(mode="json") != dataset.model_dump(mode="json"):
        raise DatasetMismatch(
            f"{experiment_dir} was produced by a different dataset; "
            "start a new run instead of resuming"
        )


def load_benchmark_dataset(path: Path) -> BenchmarkDataset:
    return BenchmarkDataset.model_validate(json.loads(path.read_text(encoding="utf-8")))


def _normalize_path(value: str | None) -> str:
    """Reduce a reported path to a comparable repo-relative form.

    Only the `./` and leading `/` prefixes are removed. `str.lstrip` takes a
    character set rather than a prefix, so stripping "./" would also mangle
    legitimate dotfile paths such as `.github/workflows/ci.yml`.
    """
    if not value:
        return ""
    text = value.replace("\\", "/").strip()
    while text.startswith("./"):
        text = text[2:]
    return text.lstrip("/")


def find_matched_keywords(comment: ReviewComment, mutant: Mutant) -> list[str]:
    """Which of the operator's keywords appear in this finding's text.

    Recorded on every outcome so a weak hit can be justified after the fact.
    A rate nobody can audit is not defensible.
    """
    haystack = f"{comment.issue} {comment.suggestion}".lower()
    return [keyword for keyword in mutant.keywords if keyword in haystack]


def _mentions_defect(comment: ReviewComment, mutant: Mutant) -> bool:
    return bool(find_matched_keywords(comment, mutant))


def score_mutant(
    mutant: Mutant,
    comments: list[ReviewComment],
    *,
    line_tolerance: int = LINE_TOLERANCE,
) -> tuple[Outcome, ReviewComment | None]:
    """Classify how well a set of findings caught one injected defect.

    A positional hit always wins over a keyword-only match, so the strongest
    available evidence decides the outcome regardless of comment ordering.
    """
    target_file = _normalize_path(mutant.file)
    same_file = [c for c in comments if _normalize_path(c.file) == target_file]

    for comment in same_file:
        if comment.line is not None and abs(comment.line - mutant.line) <= line_tolerance:
            return "hit", comment

    for comment in same_file:
        if _mentions_defect(comment, mutant):
            return "weak_hit", comment

    return "miss", None


def build_outcome(
    mutant: Mutant,
    comments: list[ReviewComment],
    *,
    repo_name: str,
    method: str,
    file_lines: int = 0,
) -> MutantOutcome:
    outcome, matched = score_mutant(mutant, comments)
    target_file = _normalize_path(mutant.file)
    return MutantOutcome(
        repo_name=repo_name,
        method=method,
        operator=mutant.operator,
        file=mutant.file,
        line=mutant.line,
        outcome=outcome,
        matched_file=matched.file if matched else None,
        matched_line=matched.line if matched else None,
        matched_issue=matched.issue if matched else None,
        matched_suggestion=matched.suggestion if matched else None,
        matched_keywords=", ".join(find_matched_keywords(matched, mutant)) if matched else "",
        total_findings=len(comments),
        findings_in_file=sum(1 for c in comments if _normalize_path(c.file) == target_file),
        file_lines=file_lines,
    )


def chance_of_hit(findings_in_file: int, file_lines: int, *, line_tolerance: int = LINE_TOLERANCE) -> float:
    """Probability that at least one of `findings_in_file` uniformly placed
    findings falls within the tolerance window of the mutated line."""
    if file_lines <= 0 or findings_in_file <= 0:
        return 0.0
    window = min(1.0, (2 * line_tolerance + 1) / file_lines)
    return 1 - (1 - window) ** findings_in_file


def summarize_outcomes(outcomes: list[MutantOutcome]) -> list[MethodSummary]:
    by_method: dict[str, list[MutantOutcome]] = defaultdict(list)
    for outcome in outcomes:
        by_method[outcome.method].append(outcome)

    summaries: list[MethodSummary] = []
    for method, rows in sorted(by_method.items()):
        scored = len(rows)
        hits = sum(1 for row in rows if row.outcome == "hit")
        weak = sum(1 for row in rows if row.outcome == "weak_hit")
        misses = sum(1 for row in rows if row.outcome == "miss")
        findings = sum(row.total_findings for row in rows)
        measurable = [row for row in rows if row.file_lines > 0]
        chance = (
            sum(chance_of_hit(row.findings_in_file, row.file_lines) for row in measurable)
            / len(measurable)
            if measurable
            else 0.0
        )
        detection = hits / scored if scored else 0.0
        summaries.append(
            MethodSummary(
                method=method,
                scored_mutants=scored,
                hits=hits,
                weak_hits=weak,
                misses=misses,
                detection_rate=round(detection, 4),
                weak_or_better_rate=round((hits + weak) / scored, 4) if scored else 0.0,
                findings_per_mutant=round(findings / scored, 2) if scored else 0.0,
                expected_by_chance=round(chance, 4),
                lift_over_chance=round(detection / chance, 2) if chance else 0.0,
            )
        )
    return summaries


class RepoMethodSummary(BaseModel):
    repo_name: str
    method: str
    scored_mutants: int
    hits: int
    detection_rate: float


def summarize_by_repo(outcomes: list[MutantOutcome]) -> list[RepoMethodSummary]:
    """Per repository and method.

    The first run's aggregate said no_context led on detection. Split by
    repository it scored 0.70 on one and 0.00 on another, so the aggregate was
    reporting a single repository rather than a property of the method. A
    summary that cannot show that is misleading by omission.
    """
    grouped: dict[tuple[str, str], list[MutantOutcome]] = defaultdict(list)
    for outcome in outcomes:
        grouped[(outcome.repo_name, outcome.method)].append(outcome)

    rows: list[RepoMethodSummary] = []
    for (repo_name, method), items in sorted(grouped.items()):
        hits = sum(1 for item in items if item.outcome == "hit")
        rows.append(
            RepoMethodSummary(
                repo_name=repo_name,
                method=method,
                scored_mutants=len(items),
                hits=hits,
                detection_rate=round(hits / len(items), 4) if items else 0.0,
            )
        )
    return rows


def resolve_reviewable_files(
    repo_root: Path,
    *,
    include_tests: bool,
    max_file_bytes: int,
    max_files: int,
) -> list[str]:
    """Reproduce the file set `context_agent` selects in repo mode.

    Mutations may only target these files. Anything outside the list is never
    shown to the reviewer, so injecting there would measure file selection
    rather than review quality.
    """
    config = load_repo_config(repo_root)
    accepted, _ = collect_repo_files(
        repo_root,
        ignore_globs=config.data["ignore_globs"],
        include_tests=include_tests,
        max_file_bytes=max_file_bytes,
    )
    return [path.as_posix() for path in prioritize_files(accepted, max_files)]


def plan_mutants(repo_root: Path, target: BenchmarkTarget, dataset: BenchmarkDataset) -> list[Mutant]:
    reviewable = resolve_reviewable_files(
        repo_root,
        include_tests=target.include_tests,
        max_file_bytes=target.max_file_bytes,
        max_files=target.max_files,
    )
    return select_mutants(
        repo_root,
        reviewable,
        operators=tuple(dataset.operators),
        count=dataset.mutants_per_repo,
        seed=dataset.seed,
    )


class ReviewRunner(Protocol):
    """Reviews a prepared working copy. Injected so the harness is testable offline."""

    async def __call__(
        self,
        *,
        repo_root: Path,
        repo_name: str,
        method: EvaluationMethod,
        dataset: BenchmarkDataset,
        target: BenchmarkTarget,
    ) -> list[ReviewComment]: ...


async def pipeline_review_runner(
    *,
    repo_root: Path,
    repo_name: str,
    method: EvaluationMethod,
    dataset: BenchmarkDataset,
    target: BenchmarkTarget,
) -> list[ReviewComment]:
    """Run the real pipeline against an already-cloned, already-mutated copy.

    `cloner_agent` is skipped deliberately: it would re-clone from GitHub and
    discard the injected defect.
    """
    from .evaluation import PreparedRun, single_agent_review
    from .models import ProjectContext, ReviewRequest, ReviewState
    from .provider import normalize_comments
    from .workflow import context_agent, review_agent

    request = ReviewRequest(
        github_url=target.github_url,
        provider=dataset.provider,
        model=dataset.model,
        max_files=target.max_files,
        max_file_bytes=target.max_file_bytes,
        include_tests=target.include_tests,
    )

    if method == "no_context":
        # Running ContextAgent only to throw its output away would spend a call
        # per mutant and make the ablation impure. The file list it also
        # produces is derivable without an LLM.
        state = ReviewState(
            request=request,
            workspace_dir=str(repo_root),
            repo_name=repo_name,
            files_to_review=resolve_reviewable_files(
                repo_root,
                include_tests=target.include_tests,
                max_file_bytes=target.max_file_bytes,
                max_files=target.max_files,
            ),
            project_context=ProjectContext(
                readme_summary="ContextAgent disabled for ablation.",
                folder_summary="ContextAgent disabled for ablation.",
                key_files=[],
                architecture_notes=[],
            ),
        )
    else:
        state = ReviewState(request=request, workspace_dir=str(repo_root), repo_name=repo_name)
        state = await context_agent(state)
    assert state.project_context is not None

    if method == "single_agent":
        prepared = PreparedRun(
            request=request,
            repo_name=repo_name,
            workspace_dir=repo_root,
            files_to_review=state.files_to_review,
            skipped_files=state.skipped_files,
            project_context=state.project_context,
        )
        return normalize_comments(await single_agent_review(prepared, state.project_context))

    state = await review_agent(state)
    # `no_priority` intentionally skips normalization -- that omission is the ablation.
    if method == "no_priority":
        return state.comments
    return normalize_comments(state.comments)


async def run_benchmark_dataset(
    dataset: BenchmarkDataset,
    *,
    output_root: Path,
    workspace_root: Path,
    review_runner: ReviewRunner = pipeline_review_runner,
    resume_from: Path | None = None,
) -> tuple[Path, list[MutantOutcome]]:
    if resume_from is not None:
        experiment_dir = resume_from
        _assert_same_dataset(experiment_dir, dataset)
        already = completed_units(experiment_dir)
    else:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        experiment_dir = output_root / f"{dataset.name}-{stamp}"
        experiment_dir.mkdir(parents=True, exist_ok=True)
        (experiment_dir / "dataset.json").write_text(
            json.dumps(dataset.model_dump(mode="json"), indent=2), encoding="utf-8"
        )
        already = set()

    outcomes: list[MutantOutcome] = _load_jsonl(
        experiment_dir / "mutant-outcomes.jsonl", MutantOutcome
    ) if resume_from is not None else []
    errors: list[BenchmarkError] = _load_jsonl(
        experiment_dir / "errors.jsonl", BenchmarkError
    ) if resume_from is not None else []
    unreachable: list[dict[str, str]] = []
    # Appended to as results arrive. A run can take over an hour against a
    # rate-limited provider, and losing all of it to a late failure is worse
    # than the cost of writing each row twice.
    outcomes_log = experiment_dir / "mutant-outcomes.jsonl"
    errors_log = experiment_dir / "errors.jsonl"

    for target in dataset.targets:
        parsed = parse_github_url(target.github_url)
        # Identify by owner/repo: two targets can share a repo name, and
        # collapsing them would silently merge their outcomes.
        repo_name = parsed.full_name
        slug = f"{parsed.owner}-{parsed.repo}"
        pristine = workspace_root / "pristine" / slug
        # git clone and the working-copy duplication are blocking; keep them
        # off the event loop so this coroutine stays usable from a server.
        await asyncio.to_thread(clone_repo, target.github_url, pristine)

        _apply_target_config(pristine, target)
        mutants = plan_mutants(pristine, target, dataset)
        if not mutants:
            unreachable.append(
                {
                    "repo": repo_name,
                    "github_url": target.github_url,
                    "reason": "no reviewable Python file yielded a valid mutant",
                }
            )
            continue

        for index, mutant in enumerate(mutants):
            pending = [
                method
                for method in dataset.methods
                if _unit_key(repo_name, mutant.operator, mutant.file, mutant.line, method)
                not in already
            ]
            if not pending:
                continue
            mutated = workspace_root / "mutated" / f"{slug}-{index}"
            await asyncio.to_thread(_copy_tree, pristine, mutated)
            apply_mutant(mutated, mutant)
            for method in pending:
                try:
                    comments = await review_runner(
                        repo_root=mutated,
                        repo_name=repo_name,
                        method=method,
                        dataset=dataset,
                        target=target,
                    )
                except Exception as exc:  # noqa: BLE001 - one failure must not end the run
                    error = BenchmarkError(
                        repo_name=repo_name,
                        method=method,
                        operator=mutant.operator,
                        file=mutant.file,
                        line=mutant.line,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                    errors.append(error)
                    _append_jsonl(errors_log, error)
                    continue
                outcome = build_outcome(
                    mutant,
                    comments,
                    repo_name=repo_name,
                    method=method,
                    file_lines=_count_lines(mutated / mutant.file),
                )
                outcomes.append(outcome)
                _append_jsonl(outcomes_log, outcome)
            shutil.rmtree(mutated, ignore_errors=True)

    export_outcomes(outcomes, experiment_dir / "mutant-outcomes.csv")
    summaries = summarize_outcomes(outcomes)
    export_summary(summaries, experiment_dir)
    export_by_repo(summarize_by_repo(outcomes), experiment_dir / "benchmark-by-repo.csv")
    if unreachable:
        (experiment_dir / "unreachable.json").write_text(
            json.dumps(unreachable, indent=2), encoding="utf-8"
        )
    succeeded = {
        _unit_key(o.repo_name, o.operator, o.file, o.line, o.method) for o in outcomes
    }
    errors = [
        e
        for e in errors
        if _unit_key(e.repo_name, e.operator, e.file, e.line, e.method) not in succeeded
    ]
    errors_json = experiment_dir / "errors.json"
    if errors:
        errors_json.write_text(
            json.dumps([e.model_dump() for e in errors], indent=2), encoding="utf-8"
        )
    else:
        # A resume can clear every earlier failure; leaving the previous file in
        # place would report failures that no longer exist.
        errors_json.unlink(missing_ok=True)
    return experiment_dir, outcomes


def _apply_target_config(repo_root: Path, target: BenchmarkTarget) -> None:
    """Write the target's ignore patterns into the clone.

    Uses the product's own .repo-reviewer.toml extension point, so mutation
    planning and the review agents resolve the identical file list rather than
    two independently computed ones.
    """
    if not target.ignore_globs:
        return
    config = load_repo_config(repo_root)
    globs = list(config.data["ignore_globs"]) + list(target.ignore_globs)
    lines = ["ignore_globs = ["]
    lines += [f'  "{glob}",' for glob in globs]
    lines.append("]")
    (repo_root / ".repo-reviewer.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _count_lines(path: Path) -> int:
    try:
        return len(path.read_text(encoding="utf-8", errors="ignore").splitlines())
    except OSError:
        return 0


def _append_jsonl(path: Path, record: BaseModel) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record.model_dump(mode="json")) + "\n")


def _copy_tree(source: Path, destination: Path) -> None:
    if destination.exists():
        shutil.rmtree(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, destination, ignore=shutil.ignore_patterns(".git"))


def export_outcomes(outcomes: list[MutantOutcome], destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(MutantOutcome.model_fields.keys())
    with destination.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for outcome in outcomes:
            writer.writerow(outcome.model_dump())


def export_by_repo(rows: list[RepoMethodSummary], destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(RepoMethodSummary.model_fields.keys())
    with destination.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row.model_dump())


def export_summary(summaries: list[MethodSummary], destination_root: Path) -> tuple[Path, Path, Path]:
    destination_root.mkdir(parents=True, exist_ok=True)
    csv_path = destination_root / "benchmark-summary.csv"
    json_path = destination_root / "benchmark-summary.json"
    tex_path = destination_root / "benchmark-summary.tex"

    fieldnames = list(MethodSummary.model_fields.keys())
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for summary in summaries:
            writer.writerow(summary.model_dump())

    json_path.write_text(
        json.dumps([s.model_dump() for s in summaries], indent=2), encoding="utf-8"
    )
    tex_path.write_text(_to_latex_table(summaries), encoding="utf-8")
    return csv_path, json_path, tex_path


def _to_latex_table(summaries: list[MethodSummary]) -> str:
    header = (
        "\\begin{tabular}{lrrrrrr}\n\\hline\n"
        "Method & Mutants & Detection & Chance & Lift & Weak+ & Findings/mutant \\\\\n"
        "\\hline\n"
    )
    body = "".join(
        f"{s.method.replace('_', ' ')} & {s.scored_mutants} & {s.detection_rate:.3f} "
        f"& {s.expected_by_chance:.3f} & {s.lift_over_chance:.2f} "
        f"& {s.weak_or_better_rate:.3f} & {s.findings_per_mutant:.2f} \\\\\n"
        for s in summaries
    )
    return header + body + "\\hline\n\\end{tabular}\n"


def run_benchmark_sync(
    dataset_path: Path,
    output_root: Path,
    workspace_root: Path,
    resume_from: Path | None = None,
) -> tuple[Path, list[MutantOutcome]]:
    dataset = load_benchmark_dataset(dataset_path)
    return asyncio.run(
        run_benchmark_dataset(
            dataset,
            output_root=output_root,
            workspace_root=workspace_root,
            resume_from=resume_from,
        )
    )
