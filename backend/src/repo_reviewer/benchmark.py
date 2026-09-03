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
    total_findings: int = 0
    findings_in_file: int = 0


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


def _mentions_defect(comment: ReviewComment, mutant: Mutant) -> bool:
    haystack = f"{comment.issue} {comment.suggestion}".lower()
    return any(keyword in haystack for keyword in mutant.keywords)


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
        total_findings=len(comments),
        findings_in_file=sum(1 for c in comments if _normalize_path(c.file) == target_file),
    )


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
        summaries.append(
            MethodSummary(
                method=method,
                scored_mutants=scored,
                hits=hits,
                weak_hits=weak,
                misses=misses,
                detection_rate=round(hits / scored, 4) if scored else 0.0,
                weak_or_better_rate=round((hits + weak) / scored, 4) if scored else 0.0,
                findings_per_mutant=round(findings / scored, 2) if scored else 0.0,
            )
        )
    return summaries


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
) -> tuple[Path, list[MutantOutcome]]:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    experiment_dir = output_root / f"{dataset.name}-{stamp}"
    experiment_dir.mkdir(parents=True, exist_ok=True)
    (experiment_dir / "dataset.json").write_text(
        json.dumps(dataset.model_dump(mode="json"), indent=2), encoding="utf-8"
    )

    outcomes: list[MutantOutcome] = []
    errors: list[BenchmarkError] = []
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
            mutated = workspace_root / "mutated" / f"{slug}-{index}"
            await asyncio.to_thread(_copy_tree, pristine, mutated)
            apply_mutant(mutated, mutant)
            for method in dataset.methods:
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
                outcome = build_outcome(mutant, comments, repo_name=repo_name, method=method)
                outcomes.append(outcome)
                _append_jsonl(outcomes_log, outcome)
            shutil.rmtree(mutated, ignore_errors=True)

    export_outcomes(outcomes, experiment_dir / "mutant-outcomes.csv")
    summaries = summarize_outcomes(outcomes)
    export_summary(summaries, experiment_dir)
    if unreachable:
        (experiment_dir / "unreachable.json").write_text(
            json.dumps(unreachable, indent=2), encoding="utf-8"
        )
    if errors:
        (experiment_dir / "errors.json").write_text(
            json.dumps([e.model_dump() for e in errors], indent=2), encoding="utf-8"
        )
    return experiment_dir, outcomes


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
        "\\begin{tabular}{lrrrr}\n\\hline\n"
        "Method & Mutants & Detection & Weak+ & Findings/mutant \\\\\n\\hline\n"
    )
    body = "".join(
        f"{s.method.replace('_', ' ')} & {s.scored_mutants} & {s.detection_rate:.3f} "
        f"& {s.weak_or_better_rate:.3f} & {s.findings_per_mutant:.2f} \\\\\n"
        for s in summaries
    )
    return header + body + "\\hline\n\\end{tabular}\n"


def run_benchmark_sync(
    dataset_path: Path,
    output_root: Path,
    workspace_root: Path,
) -> tuple[Path, list[MutantOutcome]]:
    dataset = load_benchmark_dataset(dataset_path)
    return asyncio.run(
        run_benchmark_dataset(dataset, output_root=output_root, workspace_root=workspace_root)
    )
