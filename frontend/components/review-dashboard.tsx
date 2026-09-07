"use client";

import { Fragment, FormEvent, useEffect, useState } from "react";

import type { ProgressEvent, ReviewComment, ReviewJob, ReviewResult, Severity } from "../lib/types";
import { demoRuns } from "../demo";
import type { Citation, DemoRun, FindingCheck, Verdict } from "../demo/types";

// Resolved at build time, and deliberately empty when unset rather than
// defaulting to localhost. The hosted demo is served over https, where a
// request to a local address is blocked as mixed content -- so a baked-in
// default would leave the page issuing requests that can never succeed and
// give a visitor no way to point it anywhere else. Empty means the page runs
// on the recorded review alone; `npm run dev` and a self-hosted build supply
// the value through NEXT_PUBLIC_API_BASE.
const buildTimeApiBase = process.env.NEXT_PUBLIC_API_BASE ?? "";

/** Reject a backend the browser will refuse to call from the current page. */
export function mixedContentWarning(base: string): string | null {
  if (typeof window === "undefined" || !base.startsWith("http://")) {
    return null;
  }
  if (window.location.protocol !== "https:") {
    return null;
  }
  let host = "";
  try {
    host = new URL(base).hostname;
  } catch {
    host = "";
  }
  if (host === "localhost" || host === "127.0.0.1") {
    return null;
  }
  return "This page is served over https, so the browser will block a plain http backend. Use an https address.";
}

const hintStyle = {
  color: "var(--ink-2)",
  fontSize: "0.85rem",
  lineHeight: 1.5,
  margin: "6px 0 0",
} as const;

const VERDICT_LABELS: Record<Verdict, string> = {
  accurate: "Checked: accurate",
  misplaced: "Checked: right issue, wrong line",
  "false-positive": "Checked: false positive",
  unverified: "Checked: could not settle",
};

// The commit the recorded review ran against, so a row can link to the code
// the reviewer actually saw rather than to a branch that has moved since.
/**
 * A link into the code a finding points at, or null.
 *
 * This was hardcoded to psf/requests at the recorded run's commit, which is
 * right for the recording and wrong for every live review: a run against any
 * other repository sent "view source" into requests at a commit that has
 * nothing to do with it.
 *
 * Returns null rather than guessing. A line number means nothing without the
 * commit it was computed against, so with no commit there is no link -- the
 * same reason the checking refuses to claim a position it cannot support.
 * Only github.com is linkable; other hosts do not share the blob URL shape.
 */
export function sourceLink(
  repoUrl: string,
  commit: string | undefined,
  file: string,
  line: number | null,
): string | null {
  if (!commit || !repoUrl) return null;
  let path: string;
  try {
    const url = new URL(repoUrl);
    if (url.hostname !== "github.com") return null;
    path = url.pathname.replace(/\.git$/, "").replace(/^\/+|\/+$/g, "");
  } catch {
    return null;
  }
  if (!path) return null;
  return `https://github.com/${path}/blob/${commit}/${file}${line ? `#L${line}` : ""}`;
}

const VERDICT_SHORT: Record<Verdict, string> = {
  accurate: "in the code, at the line cited",
  misplaced: "in the code, at a different line",
  "false-positive": "not in the code",
  unverified: "could not be settled",
};

/*
  The same verdict at three lengths, because a number in a strip, a chip on a
  row, and a claim inside a finding are read at three different speeds. The
  glance label never stands alone: the strip sits directly above the findings
  that carry the full wording.
*/
const VERDICT_GLANCE: Record<Verdict, string> = {
  accurate: "accurate",
  misplaced: "wrong line",
  "false-positive": "not in the code",
  unverified: "unsettled",
};

const CITATION_LABELS: Record<Citation, string> = {
  "code line": "cites a line of code",
  "comment line": "cites a comment",
  "blank line": "cites a blank line",
  "past end of file": "cites a line past the end of the file",
  "no line number": "no line number",
};

// Colour is never the only carrier: every verdict is spelled out beside it.
const VERDICT_COLORS: Record<Verdict, { border: string; text: string }> = {
  accurate: { border: "var(--ok)", text: "var(--ok)" },
  misplaced: { border: "var(--warn)", text: "var(--warn)" },
  "false-positive": { border: "var(--bad)", text: "var(--bad)" },
  unverified: { border: "var(--ink-3)", text: "var(--ink-3)" },
};

const severityOrder: Severity[] = ["high", "medium", "low"];

const defaultForm = {
  github_url: "",
  pr_number: "",
  provider: "openai",
  model: "gpt-4.1-mini",
  max_files: 30,
  max_file_bytes: 40000,
  include_tests: true,
  output_root: "../outputs",
};

export function ReviewDashboard() {
  const [form, setForm] = useState(defaultForm);
  const [jobId, setJobId] = useState<string | null>(null);
  const [job, setJob] = useState<ReviewJob | null>(null);
  const [events, setEvents] = useState<ProgressEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [apiBase, setApiBase] = useState(buildTimeApiBase);
  const [demo, setDemo] = useState<DemoRun | null>(demoRuns[0]);

  useEffect(() => {
    if (!jobId || !apiBase) return;
    const source = new EventSource(`${apiBase}/reviews/${jobId}/events`);
    source.addEventListener("progress", (event) => {
      const payload = JSON.parse(event.data) as ProgressEvent;
      setEvents((current) => [...current, payload]);
    });
    source.addEventListener("status", async () => {
      source.close();
      const response = await fetch(`${apiBase}/reviews/${jobId}`);
      const payload = (await response.json()) as ReviewJob;
      setJob(payload);
    });
    source.onerror = () => {
      source.close();
    };
    return () => source.close();
  }, [jobId, apiBase]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!apiBase) {
      setError(
        "No backend configured. This is the hosted demo, which ships a recorded review and calls nothing. Reviewing a repository means cloning it and running a model against it, so point Backend URL at a RepoReviewer backend you run yourself.",
      );
      return;
    }
    const mixedContent = mixedContentWarning(apiBase);
    if (mixedContent) {
      setError(mixedContent);
      return;
    }
    setSubmitting(true);
    setEvents([]);
    setError(null);
    setJob(null);
    setDemo(null);

    const payload = {
      ...form,
      pr_number: form.pr_number ? Number(form.pr_number) : null,
    };

    try {
      const response = await fetch(`${apiBase}/reviews`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!response.ok) {
        throw new Error(`Request failed with ${response.status}`);
      }
      const data = (await response.json()) as { job_id: string };
      setJobId(data.job_id);
    } catch (submitError) {
      setError(submitError instanceof Error ? submitError.message : "Unknown error");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main style={{ padding: "0 20px 80px", overflowX: "hidden" }}>
      {/* Full width, so it reads as the chrome of a tool rather than a strip
          inside the document. The reading page opposite has no bar at all. */}
      <div className="topbar">
        <div className="topbar-inner">
          <span className="wordmark">RepoReviewer</span>
          <nav aria-label="Primary">
            <a href="https://arxiv.org/abs/2603.16107">paper</a>
            <a href="https://github.com/peng1z/RepoReviewer">code</a>
            <a href="#recorded-review">example</a>
            <a href="#cite">cite</a>
          </nav>
        </div>
      </div>
      <div style={{ maxWidth: 860, margin: "0 auto", minWidth: 0 }}>
        <section
          style={{}}>
          {/* No eyebrow above the heading: the heading carries its own weight. */}
          <h1 style={{ margin: "32px 0 14px" }}>
            Multi-agent code review for GitHub repositories
          </h1>
          <p style={{ color: "var(--ink-2)", marginTop: 14 }}>
            Give it a repository or pull request. It clones the repo, builds project context,
            reviews files one at a time, ranks what it found, and writes a summary, streaming each
            agent step and ending in a JSON and Markdown report.
          </p>
          <p style={{ color: "var(--ink-2)", marginTop: 14 }}>
            <strong style={{ color: "var(--ink)" }}>
              You are reading a recorded review, not a live one.
            </strong>{" "}
            {apiBase
              ? "A backend is configured, so Run a review below will start a real one."
              : "This deployment hosts no backend and starts nothing: reviewing a repository means cloning whatever URL is typed and spending an API key. The review below was recorded and shipped with the page."}{" "}
            <a href="#recorded-review">Skip to it</a>, or read it on its own page at{" "}
            <a href={`/cases/${demoRuns[0].slug}/`}>/cases/{demoRuns[0].slug}/</a>. To run your
            own, open the panel below and point it at a backend you host.
          </p>

          <details style={{ marginTop: 20 }}>
            <summary style={{ cursor: "pointer", fontWeight: 600 }}>
              Run a review {apiBase ? "" : "(needs a backend you run)"}
            </summary>
          <form onSubmit={handleSubmit} style={{ display: "grid", gap: 16, marginTop: 24 }}>
            <label>
              <div style={labelStyle}>Backend URL</div>
              <input
                value={apiBase}
                onChange={(event) => setApiBase(event.target.value.trim())}
                placeholder="https://your-reporeviewer-backend.example.com"
                style={inputStyle}
              />
              <p style={hintStyle}>
                {apiBase
                  ? (mixedContentWarning(apiBase) ??
                    "Reviews will run on this backend.")
                  : "Empty: the page shows the recorded review below and makes no requests. Reviewing a repository means cloning it and running a model over it, so this demo does not host a backend -- point this at one you run."}
              </p>
            </label>

            <label>
              <div style={labelStyle}>GitHub URL</div>
              <input
                required
                value={form.github_url}
                onChange={(event) => setForm({ ...form, github_url: event.target.value })}
                placeholder="https://github.com/vercel/next.js"
                style={inputStyle}
              />
            </label>

            <div style={gridTwo}>
              <label>
                <div style={labelStyle}>PR Number</div>
                <input
                  value={form.pr_number}
                  onChange={(event) => setForm({ ...form, pr_number: event.target.value })}
                  placeholder="Optional"
                  style={inputStyle}
                />
              </label>
              <label>
                <div style={labelStyle}>Provider</div>
                <select
                  value={form.provider}
                  onChange={(event) => setForm({ ...form, provider: event.target.value })}
                  style={inputStyle}
                >
                  <option value="openai">OpenAI</option>
                  <option value="anthropic">Anthropic</option>
                  <option value="openrouter">OpenRouter</option>
                  <option value="groq">Groq</option>
                </select>
              </label>
            </div>

            <div style={gridTwo}>
              <label>
                <div style={labelStyle}>Model</div>
                <input
                  value={form.model}
                  onChange={(event) => setForm({ ...form, model: event.target.value })}
                  style={inputStyle}
                />
              </label>
              <label>
                <div style={labelStyle}>Max Files</div>
                <input
                  type="number"
                  value={form.max_files}
                  onChange={(event) => setForm({ ...form, max_files: Number(event.target.value) })}
                  style={inputStyle}
                />
              </label>
            </div>

            <div style={gridTwo}>
              <label>
                <div style={labelStyle}>Max File Bytes</div>
                <input
                  type="number"
                  value={form.max_file_bytes}
                  onChange={(event) => setForm({ ...form, max_file_bytes: Number(event.target.value) })}
                  style={inputStyle}
                />
              </label>
              <label style={{ display: "flex", alignItems: "end", gap: 10 }}>
                <input
                  type="checkbox"
                  checked={form.include_tests}
                  onChange={(event) => setForm({ ...form, include_tests: event.target.checked })}
                />
                <span style={{ color: "var(--ink-2)" }}>Include test files</span>
              </label>
            </div>

            <button
              type="submit"
              disabled={submitting}
              style={{
                ...buttonStyle,
                opacity: submitting ? 0.7 : 1,
              }}
            >
              {submitting ? "Starting review..." : "Run review"}
            </button>
          </form>
          </details>
          {error ? <p style={{ color: "var(--bad)" }}>{error}</p> : null}
        </section>

        {jobId || events.length > 0 ? (
        <section style={sectionStyle}>
          <div style={sectionHeaderStyle}>
            <h2 style={{ margin: 0 }}>Live Agent Progress</h2>
            <span style={{ color: "var(--ink-2)" }}>{jobId ?? "No active job"}</span>
          </div>
          <div style={{ display: "grid", gap: 10 }}>
            {events.length === 0 ? <p style={{ color: "var(--ink-2)" }}>Waiting for the first agent event.</p> : null}
            {events.map((entry, index) => (
              <article key={`${entry.stage}-${index}`} style={progressCardStyle}>
                <div style={{ display: "flex", justifyContent: "space-between", gap: 16 }}>
                  <strong>{entry.stage}</strong>
                  <span>{entry.percent}%</span>
                </div>
                <p style={{ margin: "8px 0 0", color: "var(--ink-2)" }}>{entry.message}</p>
              </article>
            ))}
          </div>
        </section>
        ) : null}

        {demo ? <VerificationPanel run={demo} /> : null}

        {job?.result ? (
          <ResultPanel result={job.result} jobId={job.id} apiBase={apiBase} />
        ) : null}
        {demo ? (
          <ResultPanel
            result={demo.result}
            jobId={demo.slug}
            apiBase=""
            checks={demo.checks}
            commit={demo.commit}
          />
        ) : null}

        <Citation />
      </div>
    </main>
  );
}

function VerificationPanel({ run }: { run: DemoRun }) {
  const citations = new Map<Citation, number>();
  const verdicts = new Map<Verdict, number>();
  for (const check of run.checks) {
    citations.set(check.citation, (citations.get(check.citation) ?? 0) + 1);
    if (check.verdict) {
      verdicts.set(check.verdict, (verdicts.get(check.verdict) ?? 0) + 1);
    }
  }
  const checkedCount = run.checks.filter((check) => check.verdict).length;

  return (
    <section id="recorded-review" style={sectionStyle}>
      <h2 style={{ margin: 0 }}>Recorded review, checked against the code</h2>
      <p style={{ color: "var(--ink-2)", lineHeight: 1.6, marginTop: 12 }}>
        A real run against{" "}
        <a href={`https://github.com/${run.label}/tree/${run.commit}`}>{run.label}</a> at{" "}
        <code>{run.commit.slice(0, 7)}</code>, finishing in {run.elapsedSeconds}s. Nothing in the
        output was edited, and every finding carries what was checked about it.
      </p>

      {/* The numbers first. A reader deciding whether to spend time here is
          asking what it found and how much of that survived checking, and
          that answer was previously four paragraphs down. */}
      <div className="glance">
        <div className="glance-item">
          <div className="glance-value">{run.checks.length}</div>
          <div className="glance-label">findings</div>
        </div>
        <div className="glance-item">
          <div className="glance-value">{checkedCount}</div>
          <div className="glance-label">read by hand</div>
        </div>
        {[...verdicts.entries()].map(([verdict, count]) => (
          <div className="glance-item" key={verdict}>
            <div className="glance-value" style={{ color: VERDICT_COLORS[verdict].text }}>
              {count}
            </div>
            <div className="glance-label">{VERDICT_GLANCE[verdict]}</div>
          </div>
        ))}
      </div>

      <details style={{ marginTop: 22 }}>
        <summary style={{ cursor: "pointer", fontWeight: 600 }}>
          How the two checks were done, and what they do not establish
        </summary>

        <h3 style={{ marginBottom: 4 }}>Where the line numbers point</h3>
        <p style={{ color: "var(--ink-2)", margin: "0 0 12px", lineHeight: 1.6 }}>
          Mechanical, so it covers all {run.checks.length} findings and anyone can redo it from
          the commit above.
        </p>
        <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
          {[...citations.entries()]
            .sort((a, b) => b[1] - a[1])
            .map(([citation, count]) => (
              <span
                key={citation}
                style={pillStyle(citation === "code line" ? "var(--ok)" : "var(--warn)")}
              >
                {CITATION_LABELS[citation]} <strong>{count}</strong>
              </span>
            ))}
        </div>

        <h3 style={{ margin: "22px 0 4px" }}>Whether the problem is really there</h3>
        <p style={{ color: "var(--ink-2)", margin: "0 0 12px", lineHeight: 1.6 }}>
          This needs judgement, so it was done by hand and only for the {checkedCount}{" "}
          high-severity findings. The other {run.checks.length - checkedCount} carry the
          positional check alone and are not claimed to be right or wrong.
        </p>

        <p style={{ color: "var(--ink-2)", lineHeight: 1.6, marginTop: 18 }}>
          The bottleneck is placing an issue, not finding one. A citation that sends a reviewer
          to unrelated code costs more trust than the observation earns. Positions that cannot
          hold a finding -- past the end of a file, or blank -- are now cleared automatically and
          the finding reported without one; that is a floor, not a fix, because a line can be
          wrong while still containing code.
        </p>
      </details>
    </section>
  );
}

/**
 * What the checking established, stated before the model's own summary.
 *
 * The model's headline named two defects as requiring immediate fixes; both
 * were checked and neither is in the code. Three of its five top findings were
 * false positives. Every finding below carried its verdict, but the summary --
 * the loudest text on the page -- carried none, so the page asserted defects in
 * someone else's repository and corrected itself hundreds of pixels lower.
 *
 * The model's summary is kept verbatim and collapsed beneath this, labelled.
 * Nothing is rewritten; the order and the labelling change.
 */
/**
 * Four things about a finding that are routinely collapsed into one, and are
 * not the same claim.
 *
 *   severity  -- the model's own rating. Its claim, not a check.
 *   citation  -- whether the line it cites can hold what it describes.
 *                Mechanical, every finding, no judgement.
 *   issue     -- whether the described problem is in the code. Needs reading.
 *   status    -- whether anyone read it. Absent is not "fine".
 *
 * A finding can cite a real line of code and still be wrong about it, and it
 * can be right about a real defect while pointing somewhere unrelated. Showing
 * one badge lets a reader take a positional pass for a substantive one.
 */
function CheckAxes({ comment, check }: { comment: ReviewComment; check: FindingCheck }) {
  const rows: { label: string; value: string; color?: string }[] = [
    { label: "Model severity", value: comment.severity },
    { label: "Line citation", value: CITATION_LABELS[check.citation] },
    {
      label: "Issue itself",
      value: check.verdict ? VERDICT_SHORT[check.verdict] : "not established",
      color: check.verdict ? VERDICT_COLORS[check.verdict].text : undefined,
    },
    {
      label: "Check status",
      value: check.verdict ? "read against the source by hand" : "not read by hand",
    },
  ];

  return (
    <div style={{ margin: "12px 0 16px" }}>
      <dl
        style={{
          display: "grid",
          gridTemplateColumns: "max-content 1fr",
          columnGap: 18,
          rowGap: 2,
          margin: 0,
          fontSize: "0.9rem",
        }}
      >
        {rows.map((row) => (
          <Fragment key={row.label}>
            <dt style={{ color: "var(--ink-3)" }}>{row.label}</dt>
            <dd style={{ margin: 0, color: row.color ?? "var(--ink)", fontWeight: 550 }}>
              {row.value}
            </dd>
          </Fragment>
        ))}
      </dl>
      {check.note ? (
        <p
          style={{
            margin: "10px 0 0",
            paddingLeft: 14,
            borderLeft: "1px solid var(--rule-strong)",
            color: "var(--ink-2)",
            fontSize: "0.92rem",
          }}
        >
          {check.note}
        </p>
      ) : null}
    </div>
  );
}

function CheckedSummary({
  result,
  checks,
}: {
  result: ReviewResult;
  checks: FindingCheck[];
}) {
  const handChecked = checks.filter((check) => check.verdict);
  const tally = handChecked.reduce<Record<string, number>>((acc, check) => {
    acc[check.verdict as string] = (acc[check.verdict as string] ?? 0) + 1;
    return acc;
  }, {});
  const supported = result.comments.filter((_, index) => checks[index]?.verdict === "accurate");
  const realButMisplaced = result.comments.filter(
    (_, index) => checks[index]?.verdict === "misplaced",
  );

  return (
    <div
      style={{
        borderLeft: "3px solid var(--accent)",
        background: "var(--paper-2)",
        padding: "16px 18px",
        marginBottom: 16,
      }}
    >
      <h3 style={{ margin: 0 }}>What the checking found</h3>
      <p style={{ color: "var(--ink-2)", lineHeight: 1.6, margin: "10px 0 0" }}>
        Of {result.comments.length} findings, {handChecked.length} high-severity ones were read
        against the source at the reviewed commit. {tally.accurate ?? 0} described a real problem
        at the line cited, {tally.misplaced ?? 0} described a real problem at the wrong line, and{" "}
        {tally["false-positive"] ?? 0} described something that is not in the code. The remaining{" "}
        {checks.length - handChecked.length} were not read by hand and are not claimed either way.
      </p>
      {supported.length + realButMisplaced.length > 0 ? (
        <>
          <p style={{ margin: "14px 0 6px", fontWeight: 600 }}>
            Supported by the check ({supported.length + realButMisplaced.length}):
          </p>
          <ul style={{ margin: 0, paddingLeft: 20, lineHeight: 1.6 }}>
            {[...supported, ...realButMisplaced].map((comment) => (
              <li key={`${comment.file}-${comment.issue}`}>
                <code>{comment.file}</code> — {comment.issue}
              </li>
            ))}
          </ul>
        </>
      ) : null}
      <p style={{ color: "var(--ink-2)", lineHeight: 1.6, margin: "14px 0 0" }}>
        This is one review of one repository. It does not measure the system's accuracy, and the
        by-hand verdicts are themselves claims about the code, each with its evidence on the
        finding it belongs to.
      </p>
    </div>
  );
}

function ResultPanel({
  result,
  jobId,
  apiBase,
  checks,
  commit,
}: {
  result: ReviewResult;
  jobId: string;
  apiBase: string;
  checks?: FindingCheck[];
  /** The commit reviewed. Absent for a live run, which carries no sha. */
  commit?: string;
}) {
  return (
    <section style={sectionStyle}>
      <div style={sectionHeaderStyle}>
        <div>
          <h2 style={{ margin: 0 }}>Final Review</h2>
          <p style={{ color: "var(--ink-2)", margin: "8px 0 0" }}>
            {result.repo_name} · {result.provider}/{result.model}
          </p>
        </div>
        {/* The artifacts live on the backend that produced them. With no
            backend configured -- the hosted demo -- these would be dead links,
            so they are omitted rather than shown broken. */}
        {apiBase ? (
          <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
            <a href={`${apiBase}/reviews/${jobId}/artifacts/review.json`} style={buttonStyle}>
              Download JSON
            </a>
            <a href={`${apiBase}/reviews/${jobId}/artifacts/review.md`} style={buttonStyle}>
              Download Markdown
            </a>
          </div>
        ) : null}
      </div>

      {checks ? <CheckedSummary result={result} checks={checks} /> : null}

      <details style={summaryBoxStyle} open={!checks}>
        <summary style={{ cursor: "pointer", fontWeight: 600 }}>
          {checks
            ? "Model summary, unedited — contains claims the checking did not support"
            : "Summary"}
        </summary>
        <strong style={{ display: "block", marginTop: 12 }}>{result.summary.headline}</strong>
        <ul style={{ margin: "12px 0 0", paddingLeft: 20 }}>
          {result.summary.top_findings.map((finding) => (
            <li key={finding}>{finding}</li>
          ))}
        </ul>
        {/* summary.skipped_notes was produced by the backend and never shown,
            so the coverage line -- how much of the repository was actually
            read -- did not reach the reader. */}
        {result.summary.skipped_notes.length > 0 ? (
          <ul style={{ margin: "14px 0 0", paddingLeft: 20, color: "var(--ink-2)" }}>
            {result.summary.skipped_notes.map((note) => (
              <li key={note}>{note}</li>
            ))}
          </ul>
        ) : null}
      </details>

      {severityOrder.map((severity) => {
        // Verdicts are indexed against result.comments, so the original index
        // has to survive the per-severity split.
        const items = result.comments
          .map((comment, index) => ({ comment, check: checks?.[index] }))
          .filter((entry) => entry.comment.severity === severity);
        return (
          <details key={severity} style={{ marginTop: 26 }} open={severity === "high"}>
            <summary className="group-summary">
              <span style={{ textTransform: "capitalize" }}>{severity}</span>
              <span className="group-count">
                {items.length} {items.length === 1 ? "finding" : "findings"}
              </span>
            </summary>
            <div>
              {items.length === 0 ? (
                <p style={{ color: "var(--ink-2)", marginTop: 12 }}>No {severity} findings.</p>
              ) : null}
              {items.map(({ comment, check }) => (
                <details
                  key={`${comment.file}-${comment.line}-${comment.issue}`}
                  className="finding"
                >
                  <summary>
                    <span
                      className="sev"
                      data-sev={comment.severity}
                      aria-label={`${comment.severity} severity`}
                    />
                    <span className="finding-head">
                      <span className="finding-where">
                        {comment.file}
                        {comment.line ? `:${comment.line}` : ""}
                      </span>
                      <span className="finding-what">{comment.issue}</span>
                    </span>
                    {check ? (
                      <span
                        className="verdict"
                        style={{
                          color: check.verdict
                            ? VERDICT_COLORS[check.verdict].text
                            : "var(--ink-3)",
                        }}
                      >
                        {check.verdict ? VERDICT_SHORT[check.verdict] : "not read by hand"}
                      </span>
                    ) : null}
                  </summary>
                  <div className="finding-body">
                    {check ? <CheckAxes comment={comment} check={check} /> : null}
                    <p style={{ marginBottom: 8 }}>{comment.issue}</p>
                    <p style={{ marginTop: 0, color: "var(--ink-2)" }}>{comment.suggestion}</p>
                    {comment.snippet ? (
                      <pre
                        style={{
                          borderTop: "1px solid var(--rule)",
                          borderBottom: "1px solid var(--rule)",
                          padding: "12px 0",
                          overflowX: "auto",
                        }}
                      >
                        {comment.snippet}
                      </pre>
                    ) : null}
                    {(() => {
                      const href = sourceLink(
                        result.repo_url,
                        commit,
                        comment.file,
                        comment.line,
                      );
                      return href ? (
                        <p style={{ marginTop: 12 }}>
                          <a href={href} style={{ fontSize: "0.85rem" }}>
                            view source
                          </a>
                        </p>
                      ) : null;
                    })()}
                  </div>
                </details>
              ))}
            </div>
          </details>
        );
      })}

      <SkippedFiles files={result.skipped_files} />
    </section>
  );
}

// Verbatim from https://peng1z.github.io/publications/reporeviewer/citation.bib,
// which is the authority. A second, hand-written copy drifts: mine had the
// wrong primaryClass and no DOI until it was compared against that file.
const BIBTEX = `@misc{zhang2026reporeviewer,
  title = {{RepoReviewer: A Local-First Multi-Agent Architecture for Repository-Level Code Review}},
  author = {Peng Zhang},
  year = {2026},
  eprint = {2603.16107},
  archivePrefix = {arXiv},
  primaryClass = {cs.SE},
  doi = {10.48550/arXiv.2603.16107},
  url = {https://arxiv.org/abs/2603.16107},
  note = {Version 1, preprint}
}`;

/**
 * The paper this artifact accompanies, and how to cite it.
 *
 * Kept to a footer strip rather than a hero: the review above is what a
 * visitor came for, and burying it under a paper header would make this a
 * product page for a demo. But the site had no link to the paper at all, so a
 * reader who wanted to cite the work had nowhere to go.
 */
function Citation() {
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(BIBTEX);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Clipboard access can be refused; the entry is on screen to select.
      setCopied(false);
    }
  }

  return (
    <footer id="cite" style={sectionStyle}>
      <h2 style={{ margin: 0, fontSize: "1.1rem" }}>Cite this work</h2>
      <p style={{ color: "var(--ink-2)", lineHeight: 1.6, margin: "10px 0 0" }}>
        This page is the artifact for{" "}
        <a href="https://arxiv.org/abs/2603.16107">
          RepoReviewer: A Local-First Multi-Agent Architecture for Repository-Level Code Review
        </a>
        , Peng Zhang. arXiv:2603.16107, version 1 preprint, 17 March 2026. DOI{" "}
        <a href="https://doi.org/10.48550/arXiv.2603.16107">10.48550/arXiv.2603.16107</a>.
      </p>
      <details className="drawer">
        <summary>Where the paper and this build differ</summary>
        <p className="drawer-body" style={{ color: "var(--ink-2)", lineHeight: 1.6, margin: 0 }}>
          The recording above was produced by the software at a later commit than the paper
          describes; where the two differ, the code and the recorded run are the account of what
          this build does, and the paper is the account of what version 1 reported.
        </p>
      </details>
      <div style={{ display: "flex", gap: 12, flexWrap: "wrap", margin: "14px 0 0" }}>
        <a href="https://arxiv.org/abs/2603.16107" style={buttonStyle}>
          Abstract
        </a>
        <a href="https://arxiv.org/pdf/2603.16107" style={buttonStyle}>
          PDF
        </a>
        <a href="https://peng1z.github.io/publications/reporeviewer/" style={buttonStyle}>
          Paper page
        </a>
        <a href="https://peng1z.github.io/publications/reporeviewer/citation.bib" style={buttonStyle}>
          BibTeX file
        </a>
        <a href="https://peng1z.github.io/publications/reporeviewer/citation.ris" style={buttonStyle}>
          RIS
        </a>
        <button type="button" onClick={copy} style={buttonStyle}>
          {copied ? "BibTeX copied" : "Copy BibTeX"}
        </button>
      </div>
      {/* Behind a disclosure, not gone: the clipboard can be refused, so the
          entry stays selectable one click away rather than only copyable. */}
      <details className="drawer">
        <summary>BibTeX</summary>
        <pre className="drawer-body" style={{ overflowX: "auto", fontSize: "0.82rem" }}>
          {BIBTEX}
        </pre>
      </details>
    </footer>
  );
}

function SkippedFiles({ files }: { files: { path: string; reason: string }[] }) {
  if (files.length === 0) {
    return null;
  }
  // One card per file put 150+ of them on the page and buried the only part
  // that matters: how many were left out, and why. Group by reason instead,
  // and keep the paths behind a disclosure.
  const byReason = new Map<string, string[]>();
  for (const item of files) {
    const paths = byReason.get(item.reason) ?? [];
    paths.push(item.path);
    byReason.set(item.reason, paths);
  }
  const groups = [...byReason.entries()].sort((a, b) => b[1].length - a[1].length);

  return (
    <div style={{ marginTop: 24 }}>
      <h3>Not reviewed ({files.length} files)</h3>
      <div style={{ display: "grid", gap: 8 }}>
        {groups.map(([reason, paths]) => (
          <details key={reason} style={progressCardStyle}>
            <summary style={{ cursor: "pointer" }}>
              <strong>{paths.length}</strong> · {reason}
            </summary>
            <ul style={{ margin: "10px 0 0", paddingLeft: 20, color: "var(--ink-2)" }}>
              {paths.map((path) => (
                <li key={path}>{path}</li>
              ))}
            </ul>
          </details>
        ))}
      </div>
    </div>
  );
}

function pillStyle(color: string) {
  return {
    border: `1px solid ${color}`,
    color,
    borderRadius: 999,
    padding: "6px 14px",
    fontSize: "0.9rem",
  } as const;
}

/*
  A ledger, not a deck of cards. Sections are separated by a rule and space;
  rows are separated by a rule alone. Nothing is enclosed, because 65 findings
  in 65 boxes is 65 borders between a reader and a comparison.
*/
const sectionStyle = {
  borderTop: "1px solid var(--rule)",
  paddingTop: 28,
  marginTop: 40,
};

const sectionHeaderStyle = {
  display: "flex",
  alignItems: "baseline",
  justifyContent: "space-between",
  gap: 16,
  flexWrap: "wrap" as const,
  marginBottom: 16,
};

const gridTwo = {
  display: "grid",
  gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
  gap: 16,
};

const labelStyle = {
  marginBottom: 6,
  color: "var(--ink-2)",
  fontSize: "0.9rem",
};

const inputStyle = {
  width: "100%",
  padding: "10px 12px",
  borderRadius: 4,
  border: "1px solid var(--rule-strong)",
  background: "var(--paper)",
};

const buttonStyle = {
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  gap: 8,
  borderRadius: 4,
  border: "1px solid var(--rule-strong)",
  background: "var(--paper)",
  color: "var(--ink)",
  padding: "9px 14px",
  cursor: "pointer",
  textDecoration: "none",
} as const;

const primaryButtonStyle = {
  ...buttonStyle,
  background: "var(--accent)",
  borderColor: "var(--accent)",
  color: "#fff",
} as const;

const progressCardStyle = {
  borderBottom: "1px solid var(--rule)",
  padding: "10px 0",
};

const summaryBoxStyle = {
  borderLeft: "1px solid var(--rule-strong)",
  paddingLeft: 16,
  marginTop: 20,
};

const VERDICT_INK: Record<string, string> = {
  accurate: "var(--ok)",
  misplaced: "var(--warn)",
  "false-positive": "var(--bad)",
  unverified: "var(--ink-3)",
};

/** A row in the ledger. Severity is a label in the row, not a fill behind it. */
function findingRowStyle() {
  return {
    borderTop: "1px solid var(--rule)",
    padding: "20px 0",
  };
}

