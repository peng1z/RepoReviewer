import type { ProgressEvent, ReviewResult } from "../lib/types";

/**
 * Where the finding's line number points, checked against the reviewed commit.
 *
 * This half of the check needs no judgement and covers every finding: whether
 * the cited line exists and can hold what the finding describes. Anyone can
 * reproduce it from the commit.
 */
export type Citation =
  | "code line"
  | "comment line"
  | "blank line"
  | "past end of file"
  | "no line number";

/**
 * Whether the described problem is actually in the code.
 *
 * This half needs judgement, so it was done by hand and only for the
 * high-severity findings. Publishing an AI's claims about a well-known project
 * without checking them would mean asserting defects that may not exist.
 */
export type Verdict = "accurate" | "misplaced" | "false-positive" | "unverified";

export type FindingCheck = {
  citation: Citation;
  /** Present only where the substance was checked by hand. */
  verdict?: Verdict;
  note?: string;
};

export type DemoRun = {
  slug: string;
  label: string;
  /** Wall-clock seconds the recorded review took. */
  elapsedSeconds: number;
  /** The commit that was reviewed, so every check can be redone. */
  commit: string;
  events: ProgressEvent[];
  result: ReviewResult;
  /** Indexed by position in `result.comments`. */
  checks: FindingCheck[];
};
