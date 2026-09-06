import React from "react";
import { fireEvent, render, screen, within } from "@testing-library/react";

import { ReviewDashboard } from "../components/review-dashboard";
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
    const unchecked = run.checks.filter((check) => !check.verdict).length;
    expect(screen.getAllByText(/Not checked by hand/)).toHaveLength(unchecked);

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

    const reasons = new Set(run.result.skipped_files.map((item) => item.reason));
    expect(screen.getAllByRole("group").length).toBe(reasons.size);
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
