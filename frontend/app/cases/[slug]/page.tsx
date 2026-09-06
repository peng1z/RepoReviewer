import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { demoRuns } from "../../../demo";
import type { Citation, FindingCheck, Verdict } from "../../../demo/types";

const SITE = "https://reporeviewer.peng1z.workers.dev";

const VERDICT_TEXT: Record<Verdict, string> = {
  accurate: "in the code, at the line cited",
  misplaced: "in the code, at a different line",
  "false-positive": "not in the code",
  unverified: "could not be settled",
};

const CITATION_TEXT: Record<Citation, string> = {
  "code line": "cites a line of code",
  "comment line": "cites a comment",
  "blank line": "cites a blank line",
  "past end of file": "cites a line past the end of the file",
  "no line number": "no line number",
};

export function generateStaticParams() {
  return demoRuns.map((run) => ({ slug: run.slug }));
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ slug: string }>;
}): Promise<Metadata> {
  const { slug } = await params;
  const run = demoRuns.find((item) => item.slug === slug);
  if (!run) return {};
  const url = `${SITE}/cases/${run.slug}/`;
  const byHand = run.checks.filter((check) => check.verdict).length;
  const description =
    `A recorded RepoReviewer review of ${run.label} at ${run.commit.slice(0, 7)}: ` +
    `${run.result.comments.length} findings, each with a positional check, and ${byHand} ` +
    `high-severity findings read against the source by hand.`;
  return {
    title: `${run.label} at ${run.commit.slice(0, 7)} — a checked RepoReviewer review`,
    description,
    // Each case owns its canonical. Pointing it at the paper page would ask a
    // crawler to treat a review and a paper as one document.
    alternates: { canonical: url },
    openGraph: { type: "article", url, title: run.label, description },
    twitter: { card: "summary_large_image", title: run.label, description },
  };
}

function tally(checks: FindingCheck[]) {
  return checks.reduce<Record<string, number>>((acc, check) => {
    const key = check.verdict ?? "not read by hand";
    acc[key] = (acc[key] ?? 0) + 1;
    return acc;
  }, {});
}

export default async function CasePage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  const run = demoRuns.find((item) => item.slug === slug);
  if (!run) notFound();

  const result = run.result;
  const counts = tally(run.checks);
  const byHand = run.checks.filter((check) => check.verdict).length;
  const overCap = result.skipped_files.filter((item) =>
    item.reason.startsWith("not reviewed: max_files"),
  );
  const reviewed = new Set(result.comments.map((comment) => comment.file));

  return (
    <main style={{ maxWidth: 900, margin: "0 auto", padding: "40px 20px 80px" }}>
      <nav aria-label="Primary" style={{ display: "flex", gap: 18, flexWrap: "wrap" }}>
        <a href="/">All cases</a>
        <a href="https://arxiv.org/abs/2603.16107">Paper</a>
        <a href="https://github.com/peng1z/RepoReviewer">Code</a>
      </nav>

      <header style={{ marginTop: 28 }}>
        <p style={{ letterSpacing: "0.18em", textTransform: "uppercase", color: "var(--accent)" }}>
          Recorded review
        </p>
        <h1 style={{ margin: "10px 0" }}>
          {run.label} at {run.commit.slice(0, 7)}
        </h1>
        <p style={{ color: "var(--muted)", lineHeight: 1.7 }}>
          Captured in {run.elapsedSeconds}s with {result.provider}/{result.model}. Nothing in the
          output was edited. One review of one repository: it shows what the pipeline produced on
          that occasion and measures nothing.
        </p>
      </header>

      <section style={{ marginTop: 32 }}>
        <h2>What the checking found</h2>
        <p style={{ color: "var(--muted)", lineHeight: 1.7 }}>
          {result.comments.length} findings. Every one carries a positional check: whether the line
          it cites exists and can hold what it describes. {byHand} high-severity findings were also
          read against the source by hand.
        </p>
        <dl style={{ display: "grid", gridTemplateColumns: "auto 1fr", gap: "6px 16px" }}>
          {Object.entries(counts).map(([key, count]) => (
            <div key={key} style={{ display: "contents" }}>
              <dt style={{ color: "var(--muted)" }}>
                {key in VERDICT_TEXT ? VERDICT_TEXT[key as Verdict] : key}
              </dt>
              <dd style={{ margin: 0, fontWeight: 600 }}>{count}</dd>
            </div>
          ))}
        </dl>
        <p style={{ color: "var(--muted)", lineHeight: 1.7 }}>
          The hand-checked share is not an accuracy rate for the system, and it cannot be scaled up
          to the findings nobody read.
        </p>
      </section>

      <section style={{ marginTop: 32 }}>
        <h2>Coverage</h2>
        <ul style={{ color: "var(--muted)", lineHeight: 1.8 }}>
          <li>Files that produced a finding: {reviewed.size}</li>
          <li>
            Eligible files never opened, because they ranked below the max_files cut:{" "}
            {overCap.length}
          </li>
          <li>
            Files excluded before ranking (binary, test, oversized, ignored):{" "}
            {result.skipped_files.length - overCap.length}
          </li>
        </ul>
      </section>

      <section style={{ marginTop: 32 }}>
        <h2>Findings</h2>
        {result.comments.map((comment, index) => {
          const check = run.checks[index];
          return (
            <article
              key={`${comment.file}-${index}`}
              style={{
                border: "1px solid var(--border)",
                borderRadius: 14,
                padding: 16,
                marginTop: 14,
              }}
            >
              <h3 style={{ margin: 0, fontSize: "1rem" }}>
                {comment.file}
                {comment.line ? `:${comment.line}` : ""}
              </h3>
              <dl
                style={{
                  display: "grid",
                  gridTemplateColumns: "auto 1fr",
                  gap: "2px 12px",
                  margin: "10px 0",
                  fontSize: "0.88rem",
                }}
              >
                <dt style={{ color: "var(--muted)" }}>Model severity</dt>
                <dd style={{ margin: 0 }}>{comment.severity}</dd>
                <dt style={{ color: "var(--muted)" }}>Line citation</dt>
                <dd style={{ margin: 0 }}>{CITATION_TEXT[check.citation]}</dd>
                <dt style={{ color: "var(--muted)" }}>Issue itself</dt>
                <dd style={{ margin: 0 }}>
                  {check.verdict ? VERDICT_TEXT[check.verdict] : "not established"}
                </dd>
                <dt style={{ color: "var(--muted)" }}>Check status</dt>
                <dd style={{ margin: 0 }}>
                  {check.verdict ? "read against the source by hand" : "not read by hand"}
                </dd>
              </dl>
              {check.note ? (
                <p style={{ color: "var(--muted)", lineHeight: 1.6 }}>{check.note}</p>
              ) : null}
              <p style={{ lineHeight: 1.6 }}>{comment.issue}</p>
              <p style={{ color: "var(--muted)", lineHeight: 1.6 }}>{comment.suggestion}</p>
            </article>
          );
        })}
      </section>

      <footer style={{ marginTop: 40 }}>
        <h2>Cite the method</h2>
        <p style={{ color: "var(--muted)", lineHeight: 1.7 }}>
          Produced with RepoReviewer:{" "}
          <a href="https://arxiv.org/abs/2603.16107">
            RepoReviewer: A Local-First Multi-Agent Architecture for Repository-Level Code Review
          </a>
          , Peng Zhang, arXiv:2603.16107, version 1 preprint. This review was produced by a later
          build than the paper describes, and is not an experiment the paper reports.
        </p>
      </footer>
    </main>
  );
}
