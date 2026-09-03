# Mutation-Based Benchmark Design

## Problem

RepoReviewer's evaluation (`evaluation.py`, `annotation_analysis.py`) produces its
quality numbers from a CSV that a human fills in by hand. `annotation_analysis.py`
reads `correctness`, `actionability`, `duplication`, `severity_calibration` and
`top5_usefulness` columns; nothing in the pipeline computes them. The only
automatic metric anywhere in `EvaluationRow` is `estimated_cost_usd`.

That makes every reported precision figure self-annotated by the authors, on a
small sample, with no baseline the numbers can be checked against. It is the
first thing a reviewer will challenge, and it is not something more careful
annotation can fix -- there is no ground truth to annotate against.

## Approach

Inject defects whose location and nature are known by construction, then measure
whether the reviewer reports them. Ground truth comes from the injection step,
so scoring is fully automatic and reproducible.

We deliberately do **not** use `mutmut` or `cosmic-ray`. Those tools are built
around a different question -- "does the test suite kill this mutant" -- and are
organised for running test suites. We need two things they do not provide: the
exact line a defect was introduced at, and a semantic description of the defect
so a finding can be matched on meaning rather than position alone.

## Constraints discovered in the existing code

1. **The pipeline only reviews GitHub clones.** `cloner_agent` (`workflow.py`)
   always calls `clone_repo()`, and `ReviewRequest.github_url` is an `HttpUrl`.
   The benchmark must review a locally mutated working copy, so it assembles
   `ReviewState` directly and skips `cloner_agent` -- the same seam
   `evaluation._prepare_run()` already uses. No change to `models.py` is needed.

2. **File selection truncates to `max_files`.** `prioritize_files()`
   (`repository.py`) keeps only the top `max_files` paths. A defect injected into
   a file outside that set can never be found, and would be scored as a miss for
   reasons that have nothing to do with review quality. Mutation targets are
   therefore chosen *only* from the resolved `files_to_review` list. Repos with
   no eligible target are recorded as `unreachable` and excluded from the rates.

3. **`no_priority` is the one path that skips `normalize_comments()`.** That
   omission is the operational definition of the ablation. Scoring reads the
   comments each method actually produced, and does not re-normalise them, so
   the comparison is not contaminated.

4. **Findings often carry no line number.** `ReviewComment.line` is
   `int | None`. Scoring must define an outcome for that case rather than
   treating it as a miss by default.

## Mutation operators

Every operator rewrites exactly one line, and the rewritten source must still
parse. Single-line edits keep every other line number identical to the original
file, which is what makes positional scoring meaningful.

| Operator | Transformation | Real-world defect |
|---|---|---|
| `off_by_one` | `<=` to `<`, `>=` to `>` | boundary / fencepost error |
| `negate_condition` | `if C:` to `if not (C):` | inverted branch logic |
| `swap_operator` | `+`/`-` swapped, `and`/`or` swapped | operator confusion |
| `invert_none_check` | `is None` to `is not None` | broken null guard |
| `widen_except` | `except SpecificError` to `except Exception` | swallowed exception |

`invert_none_check` inverts the guard rather than deleting it: deleting lines
would shift every subsequent line number and invalidate positional scoring.

Each mutant carries `(file, line, operator, description, keywords)`. Selection is
seeded so a dataset run is reproducible.

## Scoring

For each mutant, against the findings of one method:

- **hit** -- a finding whose `file` matches and whose `line` is within
  `LINE_TOLERANCE` (3) of the mutated line.
- **weak_hit** -- a finding whose `file` matches, whose line is absent or outside
  tolerance, but whose `issue`/`suggestion` text contains one of the operator's
  keywords.
- **miss** -- neither.

Keyword matching is used rather than an LLM judge so the metric stays
deterministic, free, and runnable in CI. It under-counts findings that describe
the defect in different words; that bias is constant across methods, so
comparisons between methods remain valid. An LLM judge can be added later as a
secondary metric.

Reported per method: `detection_rate` (hits / scored mutants),
`weak_or_better_rate`, and `findings_per_mutant` as a precision proxy -- a method
that reports everything will score well on recall alone, so the two are read
together.

## Module layout

- `mutation.py` -- operators, mutant selection, source rewriting. Pure functions
  over text and AST; no network, no LLM.
- `benchmark.py` -- orchestration, scoring, aggregation, export. The review step
  is injected as a callable so the whole harness is testable without API keys.

Both sit alongside `evaluation.py` and reuse its dataset/export conventions.
