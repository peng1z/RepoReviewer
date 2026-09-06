# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Primary: **academic peers and readers of the paper.** They arrive from
arXiv:2603.16107, from a citation, or from an AI assistant answering a question
about repository-level code review. Their job is to decide whether the system
does what the paper claims, and whether it is worth citing or building on.

Secondary, and explicitly not the design target: engineers evaluating the
author's work, and developers who might self-host the tool. Both are served by
the same evidence the academic reader wants; neither reorders it.

## Product Purpose

RepoReviewer is a local-first multi-agent system that reviews a GitHub
repository or pull request: clone, build project context, review files,
prioritise findings, summarise. It runs on the user's own machine against their
own provider key.

The hosted site is not the product. It is the **paper's runnable artifact** --
the place where a reader checks the claims without installing anything, and
finds what to cite.

## Positioning

The demo ships a real review **together with a check of that review**. Each of
the 65 findings carries what was verified about it, split by what the check
costs: a mechanical positional check over all of them, and a by-hand
substantive check of the 8 high-severity ones (1 accurate, 3 real issues at the
wrong line, 4 false positives).

Publishing a model's findings about a well-known repository without checking
them would assert defects that may not exist. Reporting the false positives at
the same weight as the correct finding is the position a neighbouring project
cannot copy by adding a feature.

## Constraints

- **Static only.** Deployed to Cloudflare Workers as assets, no Worker code in
  the request path, no build-time environment variables. Anything requiring a
  server is out of scope.
- **No hosted backend, deliberately.** Reviewing a repository means cloning
  whatever URL a visitor types and spending an API key. Live runs require the
  visitor to point Backend URL at a backend they run.
- **Content is generated, not authored.** The findings, summary and progress
  events come from a recorded pipeline run and must not be edited to read
  better. Presentation may change; the text may not.

## Evidence and Assets

- Paper: **arXiv:2603.16107**, "RepoReviewer: A Local-First Multi-Agent
  Architecture for Repository-Level Code Review", Zhang, Peng, 17 March 2026.
  PDF at https://arxiv.org/pdf/2603.16107
- The recorded review: psf/requests at commit `dae7ef6`, 344s, 65 findings.
- `docs/mutation-benchmark-results.md`: a 116-outcome mutation benchmark whose
  honest conclusion is that variance between repositories exceeds any
  difference between methods, with a section on what later work says about its
  own numbers.
- Repository: https://github.com/peng1z/RepoReviewer

## Discoverability

An explicit product goal: the work should be findable and understandable by
people and by AI assistants, so that a researcher who needs it can find it and
cite the paper.

The mechanism is indirect and worth stating precisely, because the obvious
move is wrong. **Google Scholar citations come from other papers' reference
lists, not from web pages.** Scholar's inclusion guidelines say it "does not
index pages that merely describe or link to papers", and its `citation_*` meta
tags "normally apply only to the exact page on which they're provided" -- so
putting them here would assert that this page is the article. It is not.
arXiv:2603.16107 is the article page and already carries them.

This site's job is the step before a citation: be found, be understood, and
make the paper trivial to cite once someone lands.

- an accurate title and description, not a bare product name;
- JSON-LD describing the **software** and naming the article it accompanies --
  a true statement about what this is, rather than a claim to be the paper;
- a visible link to arXiv:2603.16107 and a copyable BibTeX entry, so a reader
  who arrives here can cite without hunting;
- the evidence as crawlable text, which it already is (about 60,000 characters
  of rendered text in the static HTML).

The page currently carries three meta tags, no structured data, and **no link
to the paper at all**. The missing link is the real gap; the rest is small.

## Voice

Plain and specific. Numbers with their denominators. State what was not
checked, and what a result does not establish. No marketing register: the
reader is evaluating a claim, and overstatement is the fastest way to lose one.

## Accessibility

Findings are distinguished by verdict. Colour alone must never carry that
distinction -- each verdict is already labelled in text and must stay that way.
