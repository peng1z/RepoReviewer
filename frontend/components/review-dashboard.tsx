"use client";

import { FormEvent, useEffect, useState } from "react";

import type { ProgressEvent, ReviewJob, ReviewResult, Severity } from "../lib/types";
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
  color: "var(--muted)",
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

const CITATION_LABELS: Record<Citation, string> = {
  "code line": "cites a line of code",
  "comment line": "cites a comment",
  "blank line": "cites a blank line",
  "past end of file": "cites a line past the end of the file",
  "no line number": "no line number",
};

const VERDICT_COLORS: Record<Verdict, { border: string; text: string }> = {
  accurate: { border: "#2f7d4f", text: "#2f7d4f" },
  misplaced: { border: "#b8860b", text: "#8a6508" },
  "false-positive": { border: "#b03a3a", text: "#b03a3a" },
  unverified: { border: "#7a7a7a", text: "#666" },
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
    <main style={{ padding: "32px 20px 60px" }}>
      <div style={{ maxWidth: 1200, margin: "0 auto" }}>
        <section
          style={{
            background: "var(--panel)",
            border: "1px solid var(--border)",
            borderRadius: "var(--radius)",
            boxShadow: "var(--shadow)",
            padding: 28,
            backdropFilter: "blur(14px)",
          }}
        >
          <p style={{ letterSpacing: "0.18em", textTransform: "uppercase", color: "var(--accent)", margin: 0 }}>
            RepoReviewer
          </p>
          <h1 style={{ margin: "12px 0 10px", fontSize: "clamp(2.2rem, 5vw, 4.2rem)" }}>
            Multi-agent code review for GitHub repositories
          </h1>
          <p style={{ color: "var(--muted)", maxWidth: 760, fontSize: "1.08rem", lineHeight: 1.6 }}>
            Start a repo or pull request review locally, stream each agent step, then inspect and download the final
            JSON and Markdown reports.
          </p>

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
                <span style={{ color: "var(--muted)" }}>Include test files</span>
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
          {error ? <p style={{ color: "var(--high)" }}>{error}</p> : null}
        </section>

        <section style={{ ...sectionStyle, marginTop: 24 }}>
          <div style={sectionHeaderStyle}>
            <h2 style={{ margin: 0 }}>Live Agent Progress</h2>
            <span style={{ color: "var(--muted)" }}>{jobId ?? "No active job"}</span>
          </div>
          <div style={{ display: "grid", gap: 10 }}>
            {events.length === 0 ? <p style={{ color: "var(--muted)" }}>Run a review to stream agent updates.</p> : null}
            {events.map((entry, index) => (
              <article key={`${entry.stage}-${index}`} style={progressCardStyle}>
                <div style={{ display: "flex", justifyContent: "space-between", gap: 16 }}>
                  <strong>{entry.stage}</strong>
                  <span>{entry.percent}%</span>
                </div>
                <p style={{ margin: "8px 0 0", color: "var(--muted)" }}>{entry.message}</p>
              </article>
            ))}
          </div>
        </section>

        {demo ? <VerificationPanel run={demo} /> : null}

        {job?.result ? (
          <ResultPanel result={job.result} jobId={job.id} apiBase={apiBase} />
        ) : null}
        {demo ? (
          <ResultPanel result={demo.result} jobId={demo.slug} apiBase="" checks={demo.checks} />
        ) : null}
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
    <section style={{ ...sectionStyle, marginTop: 24 }}>
      <h2 style={{ margin: 0 }}>Recorded review, checked against the code</h2>
      <p style={{ color: "var(--muted)", lineHeight: 1.6, marginTop: 12 }}>
        A real run against{" "}
        <a href={`https://github.com/${run.label}/tree/${run.commit}`}>{run.label}</a> at{" "}
        <code>{run.commit.slice(0, 7)}</code>, finishing in {run.elapsedSeconds}s. Nothing in the
        output was edited. Publishing a model&apos;s claims about someone else&apos;s project
        without checking them would mean asserting defects that may not exist, so both halves of
        the check ship with it.
      </p>

      <h3 style={{ marginBottom: 4 }}>Where the line numbers point</h3>
      <p style={{ color: "var(--muted)", margin: "0 0 12px", lineHeight: 1.6 }}>
        Mechanical, so it covers all {run.checks.length} findings and anyone can redo it from the
        commit above.
      </p>
      <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
        {[...citations.entries()]
          .sort((a, b) => b[1] - a[1])
          .map(([citation, count]) => (
            <span key={citation} style={pillStyle(citation === "code line" ? "#2f7d4f" : "#8a6508")}>
              {CITATION_LABELS[citation]} <strong>{count}</strong>
            </span>
          ))}
      </div>

      <h3 style={{ margin: "22px 0 4px" }}>Whether the problem is really there</h3>
      <p style={{ color: "var(--muted)", margin: "0 0 12px", lineHeight: 1.6 }}>
        This needs judgement, so it was done by hand and only for the {checkedCount}{" "}
        high-severity findings. The other {run.checks.length - checkedCount} carry the positional
        check alone and are not claimed to be right or wrong.
      </p>
      <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
        {[...verdicts.entries()].map(([verdict, count]) => (
          <span key={verdict} style={pillStyle(VERDICT_COLORS[verdict].border)}>
            {VERDICT_LABELS[verdict]} <strong>{count}</strong>
          </span>
        ))}
      </div>

      <p style={{ color: "var(--muted)", lineHeight: 1.6, marginTop: 18 }}>
        The bottleneck is placing an issue, not finding one. A citation that sends a reviewer to
        unrelated code costs more trust than the observation earns. Positions that cannot hold a
        finding -- past the end of a file, or blank -- are now cleared automatically and the
        finding reported without one; that is a floor, not a fix, because a line can be wrong
        while still containing code.
      </p>
    </section>
  );
}

function ResultPanel({
  result,
  jobId,
  apiBase,
  checks,
}: {
  result: ReviewResult;
  jobId: string;
  apiBase: string;
  checks?: FindingCheck[];
}) {
  return (
    <section style={{ ...sectionStyle, marginTop: 24 }}>
      <div style={sectionHeaderStyle}>
        <div>
          <h2 style={{ margin: 0 }}>Final Review</h2>
          <p style={{ color: "var(--muted)", margin: "8px 0 0" }}>
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

      <div style={summaryBoxStyle}>
        <strong>{result.summary.headline}</strong>
        <ul style={{ margin: "12px 0 0", paddingLeft: 20 }}>
          {result.summary.top_findings.map((finding) => (
            <li key={finding}>{finding}</li>
          ))}
        </ul>
        {/* summary.skipped_notes was produced by the backend and never shown,
            so the coverage line -- how much of the repository was actually
            read -- did not reach the reader. */}
        {result.summary.skipped_notes.length > 0 ? (
          <ul style={{ margin: "14px 0 0", paddingLeft: 20, color: "var(--muted)" }}>
            {result.summary.skipped_notes.map((note) => (
              <li key={note}>{note}</li>
            ))}
          </ul>
        ) : null}
      </div>

      {severityOrder.map((severity) => {
        // Verdicts are indexed against result.comments, so the original index
        // has to survive the per-severity split.
        const items = result.comments
          .map((comment, index) => ({ comment, check: checks?.[index] }))
          .filter((entry) => entry.comment.severity === severity);
        return (
          <div key={severity} style={{ marginTop: 24 }}>
            <h3 style={{ textTransform: "capitalize" }}>{severity}</h3>
            <div style={{ display: "grid", gap: 14 }}>
              {items.length === 0 ? <p style={{ color: "var(--muted)" }}>No {severity} findings.</p> : null}
              {items.map(({ comment, check }) => (
                <article key={`${comment.file}-${comment.line}-${comment.issue}`} style={findingCardStyle(severity)}>
                  <div style={{ display: "flex", justifyContent: "space-between", gap: 16, flexWrap: "wrap" }}>
                    <strong>
                      {comment.file}
                      {comment.line ? `:${comment.line}` : ""}
                    </strong>
                    <span style={{ textTransform: "uppercase", letterSpacing: "0.1em" }}>{comment.severity}</span>
                  </div>
                  {check ? (
                    <div
                      style={{
                        border: `1px solid ${
                          check.verdict ? VERDICT_COLORS[check.verdict].border : "#c9c4bb"
                        }`,
                        borderRadius: 12,
                        padding: "10px 12px",
                        margin: "10px 0 12px",
                      }}
                    >
                      <strong
                        style={{
                          color: check.verdict ? VERDICT_COLORS[check.verdict].text : "#666",
                        }}
                      >
                        {check.verdict
                          ? VERDICT_LABELS[check.verdict]
                          : `Not checked by hand · ${CITATION_LABELS[check.citation]}`}
                      </strong>
                      {check.note ? (
                        <p style={{ margin: "6px 0 0", color: "var(--muted)", lineHeight: 1.5 }}>
                          {check.note}
                        </p>
                      ) : null}
                    </div>
                  ) : null}
                  <p style={{ marginBottom: 8 }}>{comment.issue}</p>
                  <p style={{ marginTop: 0, color: "var(--muted)" }}>{comment.suggestion}</p>
                  {comment.snippet ? (
                    <pre
                      style={{
                        background: "rgba(15, 76, 117, 0.08)",
                        borderRadius: 14,
                        padding: 14,
                        overflowX: "auto",
                      }}
                    >
                      {comment.snippet}
                    </pre>
                  ) : null}
                </article>
              ))}
            </div>
          </div>
        );
      })}

      <SkippedFiles files={result.skipped_files} />
    </section>
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
            <ul style={{ margin: "10px 0 0", paddingLeft: 20, color: "var(--muted)" }}>
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

const sectionStyle = {
  background: "var(--panel-strong)",
  border: "1px solid var(--border)",
  borderRadius: "var(--radius)",
  boxShadow: "var(--shadow)",
  padding: 24,
};

const sectionHeaderStyle = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  gap: 16,
  flexWrap: "wrap" as const,
  marginBottom: 18,
};

const gridTwo = {
  display: "grid",
  gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
  gap: 16,
};

const labelStyle = {
  marginBottom: 8,
  color: "var(--muted)",
};

const inputStyle = {
  width: "100%",
  padding: "14px 16px",
  borderRadius: 14,
  border: "1px solid var(--border)",
  background: "rgba(255,255,255,0.8)",
};

const buttonStyle = {
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  gap: 10,
  borderRadius: 999,
  border: "none",
  background: "var(--accent)",
  color: "#fff",
  padding: "14px 20px",
  cursor: "pointer",
};

const progressCardStyle = {
  borderRadius: 16,
  border: "1px solid var(--border)",
  padding: 14,
  background: "rgba(255,255,255,0.6)",
};

const summaryBoxStyle = {
  borderRadius: 18,
  padding: 18,
  background: "rgba(15, 76, 117, 0.08)",
};

function findingCardStyle(severity: Severity) {
  const colors = {
    high: "rgba(143, 29, 29, 0.14)",
    medium: "rgba(154, 91, 0, 0.12)",
    low: "rgba(30, 95, 70, 0.12)",
  };
  return {
    borderRadius: 18,
    padding: 18,
    border: "1px solid var(--border)",
    background: colors[severity],
  };
}
