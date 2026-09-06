# The recorded review

`frontend/demo/requests.json` is a real RepoReviewer run against `psf/requests`,
captured end to end and shipped with the page. The hosted demo renders it on
first paint, so it needs no backend, no API key and no running cost, and a
visitor sees a finished review immediately rather than a five-minute progress
bar.

Nothing in `result` was edited. It is exactly what the pipeline produced.

## Why it carries checks

The review makes claims about a well-known project. Publishing them unchecked
would mean asserting defects in someone else's code that may not exist -- and
in the first recording, three of three high-severity findings turned out to
describe nothing that was in the file. So each finding ships with what was
checked about it, in two parts that cost very different amounts.

### Positional, mechanical, all findings

Does the cited line exist, and can it hold what the finding describes? A blank
line cannot. Neither can a line past the end of the file. This needs no
judgement, so it covers every finding and anyone can reproduce it from the
commit named in the fixture. `scripts/position_check.py` in the recording
workflow produces it.

The same rules run inside the pipeline now (see `clear_uncitable_lines`), so a
position that cannot hold a finding is cleared before the review is reported.
The demo's positional counts are therefore what survives that check, not what
the model first said.

### Substantive, by hand, high severity only

Is the described problem really in the code? This needs judgement and cannot be
automated, so it was done by reading the source at the reviewed commit, and only
for the high-severity findings. The rest carry the positional check alone and
are labelled "Not checked by hand" -- they are not claimed to be right, and not
claimed to be wrong.

## Recapturing

Re-record whenever the pipeline changes in a way that alters its output. The
verdicts do not carry over: the model is not deterministic, so a new run
produces different findings and the substantive checks must be redone against
it. That cost is the reason the boundary is drawn at high severity.

Do not patch a recorded run to reflect a later fix. A fixture that claims to be
an unedited run has to be one; apply the fix and record again.

## Known limits, not fixed here

- `prioritize_files` ranks by `(kind, depth, path)`, so every source file ties
  on kind and is then ordered by how deep it sits. `docs/conf.py` outranks
  `src/requests/adapters.py`, and a low `max_files` spends the budget away from
  the library's core.
- Each file is reviewed on its own, so a finding cannot see cross-file usage.
  One false positive in an earlier recording called a variable unused; it was
  used in another module.
- A line number can be wrong while still pointing at code. That is the largest
  remaining category and no mechanical check catches it.
