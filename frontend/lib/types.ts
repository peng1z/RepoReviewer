export type Severity = "high" | "medium" | "low";

export type ProgressEvent = {
  stage: string;
  message: string;
  percent: number;
  payload?: Record<string, unknown> | null;
};

export type ReviewComment = {
  file: string;
  line: number | null;
  severity: Severity;
  issue: string;
  suggestion: string;
  snippet: string | null;
};

export type ReviewResult = {
  repo_url: string;
  repo_name: string;
  mode: "repo" | "pr";
  provider: string;
  model: string;
  comments: ReviewComment[];
  skipped_files: { path: string; reason: string }[];
  summary: { headline: string; top_findings: string[]; skipped_notes: string[] };
  artifacts: { json_path: string; markdown_path: string };
};

export type ReviewJob = {
  id: string;
  status: "queued" | "running" | "completed" | "failed";
  result: ReviewResult | null;
  error: string | null;
};
