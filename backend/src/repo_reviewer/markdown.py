from __future__ import annotations

from collections import defaultdict

from .models import ReviewResult


def render_review_markdown(result: ReviewResult) -> str:
    grouped: dict[str, list[str]] = defaultdict(list)
    for comment in result.comments:
        location = f"{comment.file}:{comment.line}" if comment.line else comment.file
        grouped[comment.severity].append(
            f"### {location}\n\n"
            f"**Issue:** {comment.issue}\n\n"
            f"**Suggestion:** {comment.suggestion}\n\n"
            + (f"```text\n{comment.snippet}\n```\n" if comment.snippet else "")
        )

    skipped = "\n".join(f"- `{item.path}`: {item.reason}" for item in result.skipped_files) or "- None"
    top_findings = "\n".join(f"- {finding}" for finding in result.summary.top_findings)

    tool = result.tool
    provenance = (
        [
            "",
            "## Produced with",
            f"- Tool: {tool.name} {tool.version}"
            + (f" (commit {tool.commit})" if tool.commit else " (commit unknown)"),
            f"- Source: {tool.repository}",
            f"- Method paper: {tool.method_paper}",
            f"- {tool.method_paper_note}",
        ]
        if tool
        else []
    )

    sections = [
        f"# RepoReviewer Report: {result.repo_name}",
        f"- Repository: {result.repo_url}",
        f"- Mode: {result.mode}",
        f"- Model: {result.model}",
        *provenance,
        "",
        "## Summary",
        result.summary.headline,
        "",
        top_findings,
        "",
        "## Project Context",
        f"- README: {result.context.readme_summary}",
        f"- Structure: {result.context.folder_summary}",
        "",
        "## Findings",
    ]

    for severity in ("high", "medium", "low"):
        body = "\n".join(grouped.get(severity, [])) or "_No findings._"
        sections.extend([f"## {severity.title()}", body, ""])

    sections.extend(["## Skipped Files", skipped])
    return "\n".join(sections).strip() + "\n"
