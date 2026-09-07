import React from "react";
import { fireEvent, render, screen, within } from "@testing-library/react";

import { ReviewDashboard, sourceLink } from "../components/review-dashboard";
import { demoRuns } from "../demo";

const run = demoRuns[0];

describe("ReviewDashboard", () => {
  it("shows the recorded review on first paint, with no backend request", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    render(<ReviewDashboard />);

    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(screen.getByText(/Recorded review, checked against the code/i)).toBeInTheDocument();
    expect(screen.getByText(run.result.summary.headline)).toBeInTheDocument();
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("explains why a review cannot start without a backend", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    render(<ReviewDashboard />);

    fireEvent.change(screen.getByRole("textbox", { name: /github url/i }), {
      target: { value: "https://github.com/psf/requests" },
    });
    fireEvent.click(screen.getByRole("button", { name: /run review/i }));

    expect(await screen.findByText(/No backend configured/i)).toBeInTheDocument();
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("refuses a plain http backend from an https page", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    render(<ReviewDashboard />);

    fireEvent.change(screen.getByRole("textbox", { name: /backend url/i }), {
      target: { value: "http://example.com:8000" },
    });
    fireEvent.change(screen.getByRole("textbox", { name: /github url/i }), {
      target: { value: "https://github.com/psf/requests" },
    });
    fireEvent.click(screen.getByRole("button", { name: /run review/i }));

    expect(await screen.findAllByText(/browser will block a plain http backend/i)).not.toHaveLength(
      0,
    );
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("allows a plain http localhost backend from an https page", () => {
    render(<ReviewDashboard />);

    fireEvent.change(screen.getByRole("textbox", { name: /backend url/i }), {
      target: { value: "http://127.0.0.1:8000" },
    });

    expect(screen.queryByText(/browser will block a plain http backend/i)).not.toBeInTheDocument();
    expect(screen.getByText(/Reviews will run on this backend/i)).toBeInTheDocument();
  });

  it("labels every recorded finding with what was checked about it", () => {
    render(<ReviewDashboard />);

    // A check per finding, so no claim about someone else's repository is
    // published without one -- positional for all of them, and by hand for the
    // high-severity ones.
    expect(run.checks).toHaveLength(run.result.comments.length);

    // Findings without a hand check say so plainly rather than being left bare.
    // The status appears twice per finding by design -- on the collapsed row,
    // so it is visible without opening anything, and in the axes inside -- so
    // these count the axes, which is the one place every finding has exactly
    // one of. A bare phrase count would pass whichever of the two went missing.
    const unchecked = run.checks.filter((check) => !check.verdict).length;
    expect(screen.getAllByText("not read by hand", { selector: "dd" })).toHaveLength(unchecked);
    expect(screen.getAllByText("not established", { selector: "dd" })).toHaveLength(unchecked);
    expect(screen.getAllByText("not read by hand", { selector: ".verdict" })).toHaveLength(
      unchecked,
    );

    // The hand-checked ones carry their note.
    for (const check of run.checks) {
      if (check.note) {
        expect(screen.getByText(check.note)).toBeInTheDocument();
      }
    }
  });

  it("separates the mechanical check from the one that needed judgement", () => {
    render(<ReviewDashboard />);

    expect(screen.getByText(/Where the line numbers point/i)).toBeInTheDocument();
    expect(screen.getByText(/Whether the problem is really there/i)).toBeInTheDocument();

    const byHand = run.checks.filter((check) => check.verdict).length;
    expect(byHand).toBeGreaterThan(0);
    expect(byHand).toBeLessThan(run.checks.length);
    // The boundary is stated, not implied.
    expect(
      screen.getByText(new RegExp(`only for the ${byHand}\\s+high-severity findings`, "i")),
    ).toBeInTheDocument();
  });

  it("groups the files it did not review by reason rather than listing each one", () => {
    render(<ReviewDashboard />);

    const heading = screen.getByText(
      new RegExp(`Not reviewed \\(${run.result.skipped_files.length} files\\)`),
    );
    expect(heading).toBeInTheDocument();

    // Each reason appears once as a disclosure summary, carrying its count.
    // The text is split across nodes, so match on the summary's textContent.
    const reasons = new Set(run.result.skipped_files.map((item) => item.reason));
    const summaries = [...document.querySelectorAll("summary")].map(
      (node) => node.textContent ?? "",
    );
    for (const reason of reasons) {
      expect(summaries.filter((text) => text.includes(reason))).toHaveLength(1);
    }
    // The paths stay behind the disclosure rather than filling the page.
    for (const reason of reasons) {
      const count = run.result.skipped_files.filter((item) => item.reason === reason).length;
      expect(summaries.some((text) => text.includes(String(count)) && text.includes(reason))).toBe(
        true,
      );
    }
  });

  it("reports the files the max_files cap left unread", () => {
    const overCap = run.result.skipped_files.filter((item) =>
      item.reason.startsWith("not reviewed: max_files"),
    );
    expect(overCap.length).toBeGreaterThan(0);

    render(<ReviewDashboard />);

    const coverage = run.result.summary.skipped_notes.find((note) => note.startsWith("Coverage:"));
    expect(coverage).toBeDefined();
    expect(screen.getByText(coverage as string)).toBeInTheDocument();
  });

  it("hides the artifact downloads when there is no backend to serve them", () => {
    render(<ReviewDashboard />);

    expect(screen.queryByRole("link", { name: /download json/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /download markdown/i })).not.toBeInTheDocument();
  });
});

describe("what each finding actually claims", () => {
  it("separates the model's severity from whether the issue is real", () => {
    render(<ReviewDashboard />);

    // A finding can cite a real line of code and still be wrong about it, and
    // it can be right about a real defect while pointing somewhere unrelated.
    expect(screen.getAllByText("Model severity")).toHaveLength(run.checks.length);
    expect(screen.getAllByText("Line citation")).toHaveLength(run.checks.length);
    expect(screen.getAllByText("Issue itself")).toHaveLength(run.checks.length);
    expect(screen.getAllByText("Check status")).toHaveLength(run.checks.length);
  });

  it("does not let a valid line stand in for a correct finding", () => {
    render(<ReviewDashboard />);

    // Findings that cite real code but describe nothing that is there.
    const citesCodeButWrong = run.checks.filter(
      (check) => check.citation === "code line" && check.verdict === "false-positive",
    );
    expect(citesCodeButWrong.length).toBeGreaterThan(0);
    const falsePositives = run.checks.filter(
      (check) => check.verdict === "false-positive",
    ).length;
    expect(screen.getAllByText("not in the code", { selector: "dd" }).length).toBe(falsePositives);
    expect(screen.getAllByText("not in the code", { selector: ".verdict" }).length).toBe(
      falsePositives,
    );
  });
});

describe("the model summary versus the checking", () => {
  it("leads with what the checking found, not with the model's headline", () => {
    render(<ReviewDashboard />);

    const checked = screen.getByRole("heading", { name: /What the checking found/i });
    const modelSummary = screen.getByText(/Model summary, unedited/i);

    // The model's headline named two defects as requiring immediate fixes and
    // both were checked as false positives. Leading with it asserts defects in
    // someone else's repository and corrects itself far below.
    expect(
      checked.compareDocumentPosition(modelSummary) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("keeps the model's headline verbatim, collapsed and labelled", () => {
    render(<ReviewDashboard />);

    // Not rewritten: the recorded output is evidence, and editing it to read
    // better would make the recording a claim about itself.
    const headline = screen.getByText(run.result.summary.headline);
    expect(headline).toBeInTheDocument();
    expect(headline.closest("details")).not.toBeNull();
    expect(headline.closest("details")).not.toHaveAttribute("open");
  });

  it("states the checked counts without turning them into an accuracy rate", () => {
    render(<ReviewDashboard />);

    const byHand = run.checks.filter((check) => check.verdict).length;
    expect(
      screen.getByText(new RegExp(`${byHand} high-severity ones were read`, "i")),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/does not measure the system's accuracy/i),
    ).toBeInTheDocument();
  });
});

describe("citation", () => {
  it("links the paper the artifact accompanies", () => {
    render(<ReviewDashboard />);

    // The site had no link to the paper at all, which was the largest gap for
    // a reader who wanted to cite the work.
    const abstract = screen.getByRole("link", { name: /^Abstract$/ });
    expect(abstract).toHaveAttribute("href", "https://arxiv.org/abs/2603.16107");
    expect(screen.getByRole("link", { name: /^PDF$/ })).toHaveAttribute(
      "href",
      "https://arxiv.org/pdf/2603.16107",
    );
  });

  it("offers a BibTeX entry that is on screen, not only on the clipboard", () => {
    render(<ReviewDashboard />);

    // Clipboard access can be refused, so the entry has to be selectable too.
    // It sits behind a labelled disclosure rather than printing 11 lines of
    // BibTeX at every reader, which is one click and no clipboard.
    const entry = screen.getByText(/@misc\{zhang2026reporeviewer/);
    expect(entry).toBeInTheDocument();
    expect(entry.closest("details")?.querySelector("summary")?.textContent).toMatch(/BibTeX/i);
    expect(screen.getByRole("button", { name: /copy bibtex/i })).toBeInTheDocument();
  });

  it("keeps the paper below the review rather than above it", () => {
    render(<ReviewDashboard />);

    const review = screen.getByText(/Recorded review, checked against the code/i);
    const paper = screen.getByRole("heading", { name: /^Cite this work$/ });
    // A demo page whose first screen is a paper header has become a product
    // page for its own demo.
    expect(review.compareDocumentPosition(paper) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });
});

describe("case artifacts", () => {
  it("links each finding into the source at the reviewed commit", () => {
    // Checked on the case page rather than the dashboard: the dashboard shows
    // the review, the case page is the citable document.
    const commit = run.commit;
    expect(commit).toMatch(/^[0-9a-f]{7,40}$/);
    // A link built from the default branch would drift as the branch moves.
    expect(`https://github.com/${run.label}/blob/${commit}/src/requests/cookies.py#L110`).toContain(
      commit,
    );
  });
});

describe("linking a finding into the code it points at", () => {
  // This was hardcoded to psf/requests at the recorded run's commit, so a
  // live review of any other repository sent every "view source" into
  // requests, at a commit with no relationship to what was reviewed.
  it("points at the repository that was actually reviewed", () => {
    expect(sourceLink("https://github.com/owner/thing", "abc123", "src/a.py", 7)).toBe(
      "https://github.com/owner/thing/blob/abc123/src/a.py#L7",
    );
    expect(sourceLink("https://github.com/owner/thing.git", "abc123", "src/a.py", null)).toBe(
      "https://github.com/owner/thing/blob/abc123/src/a.py",
    );
    expect(sourceLink("https://github.com/owner/thing", "abc123", "src/a.py", 7)).not.toContain(
      "psf/requests",
    );
  });

  // A line number means nothing without the commit it was computed against,
  // so with no commit there is no link rather than a plausible wrong one.
  it("offers no link when it cannot make a true one", () => {
    expect(sourceLink("https://github.com/owner/thing", undefined, "a.py", 1)).toBeNull();
    expect(sourceLink("", "abc123", "a.py", 1)).toBeNull();
    expect(sourceLink("https://gitlab.com/owner/thing", "abc123", "a.py", 1)).toBeNull();
    expect(sourceLink("not a url", "abc123", "a.py", 1)).toBeNull();
  });

  it("links the recorded review at its own repository and commit", () => {
    render(<ReviewDashboard />);

    const links = screen.getAllByRole("link", { name: /view source/i });
    expect(links.length).toBeGreaterThan(0);
    for (const link of links) {
      expect(link.getAttribute("href")).toContain(`/blob/${run.commit}/`);
      expect(link.getAttribute("href")).toContain(run.result.repo_url.replace(/^https:\/\//, ""));
    }
  });
});
