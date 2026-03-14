"use client";

import { FormEvent, useEffect, useState } from "react";

import type { ProgressEvent, ReviewJob, ReviewResult, Severity } from "../lib/types";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8000";

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

  useEffect(() => {
    if (!jobId) return;
    const source = new EventSource(`${API_BASE}/reviews/${jobId}/events`);
    source.addEventListener("progress", (event) => {
      const payload = JSON.parse(event.data) as ProgressEvent;
      setEvents((current) => [...current, payload]);
    });
    source.addEventListener("status", async () => {
      source.close();
      const response = await fetch(`${API_BASE}/reviews/${jobId}`);
      const payload = (await response.json()) as ReviewJob;
      setJob(payload);
    });
    source.onerror = () => {
      source.close();
    };
    return () => source.close();
  }, [jobId]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setEvents([]);
    setError(null);
    setJob(null);

    const payload = {
      ...form,
      pr_number: form.pr_number ? Number(form.pr_number) : null,
    };

    try {
      const response = await fetch(`${API_BASE}/reviews`, {
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

        {job?.result ? <ResultPanel result={job.result} jobId={job.id} /> : null}
      </div>
    </main>
  );
}

function ResultPanel({ result, jobId }: { result: ReviewResult; jobId: string }) {
  return (
    <section style={{ ...sectionStyle, marginTop: 24 }}>
      <div style={sectionHeaderStyle}>
        <div>
          <h2 style={{ margin: 0 }}>Final Review</h2>
          <p style={{ color: "var(--muted)", margin: "8px 0 0" }}>
            {result.repo_name} · {result.provider}/{result.model}
          </p>
        </div>
        <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
          <a href={`${API_BASE}/reviews/${jobId}/artifacts/review.json`} style={buttonStyle}>
            Download JSON
          </a>
          <a href={`${API_BASE}/reviews/${jobId}/artifacts/review.md`} style={buttonStyle}>
            Download Markdown
          </a>
        </div>
      </div>

      <div style={summaryBoxStyle}>
        <strong>{result.summary.headline}</strong>
        <ul style={{ margin: "12px 0 0", paddingLeft: 20 }}>
          {result.summary.top_findings.map((finding) => (
            <li key={finding}>{finding}</li>
          ))}
        </ul>
      </div>

      {severityOrder.map((severity) => {
        const items = result.comments.filter((comment) => comment.severity === severity);
        return (
          <div key={severity} style={{ marginTop: 24 }}>
            <h3 style={{ textTransform: "capitalize" }}>{severity}</h3>
            <div style={{ display: "grid", gap: 14 }}>
              {items.length === 0 ? <p style={{ color: "var(--muted)" }}>No {severity} findings.</p> : null}
              {items.map((comment) => (
                <article key={`${comment.file}-${comment.line}-${comment.issue}`} style={findingCardStyle(severity)}>
                  <div style={{ display: "flex", justifyContent: "space-between", gap: 16, flexWrap: "wrap" }}>
                    <strong>
                      {comment.file}
                      {comment.line ? `:${comment.line}` : ""}
                    </strong>
                    <span style={{ textTransform: "uppercase", letterSpacing: "0.1em" }}>{comment.severity}</span>
                  </div>
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

      <div style={{ marginTop: 24 }}>
        <h3>Skipped Files</h3>
        <div style={{ display: "grid", gap: 8 }}>
          {result.skipped_files.map((item) => (
            <article key={`${item.path}-${item.reason}`} style={progressCardStyle}>
              <strong>{item.path}</strong>
              <p style={{ margin: "8px 0 0", color: "var(--muted)" }}>{item.reason}</p>
            </article>
          ))}
        </div>
      </div>
    </section>
  );
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
