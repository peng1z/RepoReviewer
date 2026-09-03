# Mutation Benchmark: First Full Run

Run `mutation-benchmark-20260903-075030`, against commit `6e80e99`. 3 hours
42 minutes, 116 scored mutant/method pairs, 4 errors (3.4%).

The commit matters: the numbers below predate the metric fixes this run
prompted. The artifacts carry `matched_keywords` but not `file_lines` or
`expected_by_chance`, which places them after the auditability changes and
before the chance baseline. Re-running on a later commit will not reproduce
the weak-hit rates, by design -- the narrowed keywords remove 13 of the 31
weak hits reported here.

| Setting | Value |
|---|---|
| Repositories | pallets/click, psf/requests, encode/httpx |
| Mutants per repository | 10 |
| Methods | full, no_context, no_priority, single_agent |
| Model | `openrouter/minimax/minimax-m2.7:free` |
| Files reviewed per run | 5 (`max_files`) |
| Line tolerance for a positional hit | ±3 |
| Seed | 20260902 |

## Headline table

| Method | n | Detection | 95% CI | Weak+ | Findings/mutant |
|---|---|---|---|---|---|
| no_context | 30 | 0.267 | [0.14, 0.44] | 0.567 | 12.43 |
| full | 27 | 0.185 | [0.08, 0.37] | 0.519 | 14.93 |
| no_priority | 29 | 0.138 | [0.05, 0.31] | 0.448 | 15.21 |
| single_agent | 30 | 0.133 | [0.05, 0.30] | 0.267 | 5.43 |

Every interval overlaps every other. **No comparison between methods is
supported by this run**, which is what an n of about 30 buys; the power
calculation done beforehand put the requirement at roughly 130 per arm to
separate 0.33 from 0.50. The run was framed in advance as an effect-size probe,
and it is reported as one.

## The benchmark measures something real

A finding counts as a positional hit if it lands within ±3 lines. A method that
reports many findings will sometimes land there with no skill at all, so the
chance rate was computed per outcome from the number of findings in the mutated
file and that file's length:

    P(hit by chance) = 1 - (1 - min(1, 7 / file_lines)) ** findings_in_file

| Method | Observed | Chance | Lift |
|---|---|---|---|
| no_context | 0.267 | 0.094 | 2.83x |
| single_agent | 0.133 | 0.063 | 2.11x |
| full | 0.185 | 0.092 | 2.01x |
| no_priority | 0.138 | 0.093 | 1.49x |

All four beat chance. The reviewer is locating injected defects rather than
scattering findings that occasionally land nearby.

This also corrects a misreading of the headline table. `single_agent` looks
worst on raw detection, but it reports 5.4 findings per mutant against 13 to 15
for the others; per finding emitted it is about as efficient as `full`. **Raw
detection rate rewards verbosity**, which is why `lift_over_chance` is now
reported alongside it.

## The aggregate hid the dominant source of variance

| Method | httpx | click | requests |
|---|---|---|---|
| full | 0.22 | 0.00 | 0.33 |
| no_context | 0.00 | 0.10 | **0.70** |
| no_priority | 0.22 | 0.00 | 0.20 |
| single_agent | 0.10 | 0.00 | 0.30 |

`no_context` leads the aggregate on the strength of one repository. It scores
0.70 on requests and 0.00 on httpx — worst and best, depending where you look.

A prediction was recorded partway through the run, before the data was
complete: that `no_context` would finish above `full` on detection. It did,
0.267 to 0.185. The predicted mechanism — that repository context pushes the
model toward architectural generalities instead of specific lines — is **not**
supported. The split shows the aggregate reporting a property of `psf/requests`,
not of the method.

**Variance between repositories is larger than any difference between methods.**
That is the run's main finding, and it argues for adding repositories rather
than mutants per repository.

`click` is also worth noting: detection is at or near zero for all four methods.
Whatever makes it hard is not method-specific.

## Detection by operator

| Operator | n | Detection |
|---|---|---|
| invert_none_check | 20 | 0.30 |
| off_by_one | 20 | 0.25 |
| negate_condition | 27 | 0.19 |
| swap_operator | 27 | 0.15 |
| widen_except | 22 | 0.05 |

`widen_except` is nearly invisible to positional scoring while producing many
keyword matches — which turned out to be a problem with the keywords rather
than a property of the defect.

## A defect in the metric, found by auditing it

`matched_keywords` was added so a weak hit could be justified after the fact.
Sampling `widen_except` weak hits found that several described an unrelated
`assert` problem in a file that happened to contain a `try/except`:

> Using `assert` for runtime validation of urllib3 version. When Python runs
> with `-O` (optimize) flag, assertions are skipped.

That matched only because "except" and "exception" appear in the operator's
keyword list. Those two words fired on 11 weak hits each.

Across all operators, 8 of 31 weak hits matched **only** on such ubiquitous
words. The keyword lists have since been narrowed to phrases that name the
defect rather than the neighbourhood, and re-scoring this run's stored finding
text with the new lists removes 13 of the 31 weak hits:

| Method | weak+ before | weak+ after |
|---|---|---|
| full | 0.519 | 0.407 |
| no_context | 0.567 | 0.433 |
| no_priority | 0.448 | 0.379 |
| single_agent | 0.267 | 0.133 |

Ordering is unchanged; the absolute values were inflated. `detection_rate` is
unaffected, since positional hits never consulted keywords.

The three false positives quoted above are now regression tests, along with two
genuine matches that must keep matching.

## Errors

Four of 120 pairs failed and were excluded from the rates rather than scored as
misses.

| Repository | Method | Cause |
|---|---|---|
| pallets/click | full | `ReviewComment` validation: model omitted `file` |
| psf/requests | full | Provider 502 after 4 retries |
| encode/httpx | no_priority | `ReviewComment` validation |
| encode/httpx | — | `ReviewComment` validation |

Three of four are the same shape: the model returned a finding missing a
required field, and the whole run was discarded with it. That is heavier than
necessary — the other findings in the same response were well-formed. Skipping
the malformed entry instead is tracked as follow-up work.

## What this run changed

1. Keyword lists narrowed; ubiquitous words removed.
2. `expected_by_chance` and `lift_over_chance` added to the summary, so
   detection can be read without being confounded by verbosity.
3. `benchmark-by-repo.csv` added, so repository variance is visible instead of
   being averaged away.

## What a next run should do differently

- **More repositories, not more mutants.** Repository variance dominates.
- **Report lift, not raw detection**, when comparing methods.
- **Treat weak hits as secondary.** Even with narrowed keywords they are a
  proxy; positional hits are the defensible measure.
- An LLM judge as a second opinion on weak hits, reported separately from the
  keyword metric rather than replacing it.
