# Design

Written from the built pages, not before them.

## What this surface is

The demo is the paper's runnable artifact. A researcher arrives from
arXiv:2603.16107 or from an assistant that surfaced it, and their job is to
judge whether the system did what the paper claims. So the mode is Read, and
the recorded review leads.

But the content is not prose. It is 65 findings in parallel, each carrying four
separate claims a reader compares across rows: what the model rated it, whether
the line it cites can hold what it describes, whether the problem is in the
code, and whether anyone read it. That is a ledger, and it is set as one.

The card deck this category ships is refused. Sixty-five findings in
sixty-five boxes is sixty-five borders between a reader and the comparison they
came to make.

## Ground and colour

White, not the cream this started as. The reader has the reviewed source open
beside this page; matching it is worth more than atmosphere.

Restrained: neutrals and one accent, `--accent: #1f4e79`, on links and focus
rings only. Three verdict inks exist, and none of them ever carries meaning
alone -- every verdict is spelled out in words in the same row.

| token | value | for |
|---|---|---|
| `--ink` | `#16191d` | body text |
| `--ink-2` | `#4a5058` | secondary prose |
| `--ink-3` | `#767d86` | field labels, disclosure markers |
| `--paper` | `#ffffff` | ground |
| `--rule` | `#e2e5e8` | row and section divisions |
| `--rule-strong` | `#c8ccd1` | inputs, quote rules, scrollbar thumb |
| `--ok` / `--warn` / `--bad` | `#1a6b45` / `#8a5a00` / `#a32a26` | verdicts, always beside their words |

## Type

System sans throughout, because this page is scanned before it is read.
Tabular figures on `body`, so line numbers, counts and severities align down a
column of findings -- the whole reason the ledger is legible.

Mono is reserved for what is actually code: file paths, line numbers, commits,
and snippets. It is never a costume for "technical".

Measure is `min(68ch, 100%)` on prose; the ledger itself runs wider because
aligned fields, not line length, govern it.

## Structure

Rules divide; nothing encloses. A section separates with a top rule and 40px
of space. A finding row separates with a rule alone and 20px.

The four claims about a finding are a definition list on a
`max-content 1fr` grid, so the labels align and the values start at one column
down every row. The note that explains a verdict hangs off a left rule rather
than sitting in a tinted box.

No eyebrow above any heading.

## Motion

One authored moment: a disclosure opening, 220ms on an exponential ease-out,
behind `prefers-reduced-motion`. The model's own summary lives in one of those
disclosures, closed, because it contains claims the checking did not support.

## Browser surfaces

Selection, focus ring, and scrollbar are themed from the palette.

## Accessibility

Colour never carries a verdict alone. Severity, citation validity, issue
validity and check status are four labelled fields, not four shades.

## What is deliberately absent

Gradients, glass, blur, shadow, rounded panels, kickers, severity-tinted card
backgrounds, and any card whose only job is to hold a heading and a paragraph.

## Disclosure

65 findings printed in full made a 45,730px page. Nobody scrolls 50 screens, so
the page asserted 65 things about someone else's repository that no reader ever
reached. A finding is now a row: a severity mark, where it points, the first
line of what it claims, and the verdict, in one line. Opening it is a decision
rather than a lottery.

Severity groups are drawers. High is open because it is the one people came
for; Medium and Low carry their counts on the closed summary.

The home page is 3,196px and the case permalink 5,665px, down from 45,730 and
19,995. The permalink stays longer on purpose: it is the citable artifact and
lists every finding, but as an index rather than a wall.

## Colour

Colour means one thing here: a person read this finding against the source and
this is what they found. Green accurate, amber right-issue-wrong-line, red
not-in-the-code, grey unread -- on the row chip, in the axes, and in the strip
at the top, always with the words beside it.

Severity is deliberately *not* coloured. It is the model's own rating, not a
finding about the code, and a red dot for "high" sitting beside a red chip for
"false positive" would put two opposite meanings in one row. Severity is a
filled square at three weights instead.

The strip under the run line is the one place the page raises its voice: five
figures on an accent wash, because a reader deciding whether to spend time here
is asking what it found and how much survived checking, and that answer used to
be four paragraphs down.

## What this is not

The reading page in the sibling project and this one had converged on one
component set -- the same masthead, the same stat strip on the same accent
wash, the same drawer, the same white ground, and two blues six hex points
apart. That is what happens when a component is invented once and pasted
twice: two unrelated things start reading as one template.

This one is an instrument, so it wears an instrument's chrome:

- A dark title bar across the full width, `$ repo-reviewer` in the mono. Not a
  masthead inside the column -- chrome around it.
- Monospace for anything the machine produced or counted: the wordmark, the
  nav, the figures, the verdict chips, the counts, the paths.
- Figures in a **ruled grid** of cells with a double rule under it, the way a
  coverage report or a CI summary prints them -- not on a coloured wash.
- White ground, system sans, square corners, tight rows.

The document opposite has no bar, warm paper, a sepia accent, serif
throughout, a running head, and its particulars in a ruled band. Nothing is
shared between them but the idea of a disclosure, and they do not even mark
one the same way.

## Mark

`app/icon.svg`: a caret and a cursor on near-black. The title bar already
opens with `$ repo-reviewer`, so the mark is that prompt reduced until two
shapes are all that is left, which is all 16px holds. No lettering: a glyph
that needs to be read is not a mark.

It shares nothing with the document's mark opposite. Different ground
(near-black against paper), different palette, different geometry. At tab
size that difference is most of what a favicon does.

`app/apple-icon.png` is the same drawing rendered at 180px.

A double hyphen is illegal inside an XML comment, and this house style writes
em dashes as double hyphens. The first version of the file shipped with one,
which makes the SVG unparseable and renders it as a broken image with no error
in the console and no failing test. The file carries a note saying so.
