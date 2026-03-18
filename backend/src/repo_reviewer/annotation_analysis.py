from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path


def summarize_annotations(annotation_sheet: Path, destination_root: Path) -> tuple[Path, Path]:
    rows = list(_read_rows(annotation_sheet))
    by_method: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        by_method[row["method"]].append(row)

    summary_rows: list[dict[str, str | float | int]] = []
    for method, method_rows in sorted(by_method.items()):
        summary_rows.append(
            {
                "method": method,
                "findings": len(method_rows),
                "precision": _fraction(method_rows, "correctness", {"correct"}),
                "partial_or_better": _fraction(method_rows, "correctness", {"correct", "partially_correct"}),
                "actionable_rate": _fraction(method_rows, "actionability", {"actionable"}),
                "actionable_or_better": _fraction(method_rows, "actionability", {"actionable", "somewhat_actionable"}),
                "duplicate_rate": _fraction(method_rows, "duplication", {"duplicate"}),
                "severity_agreement": _fraction(method_rows, "severity_calibration", {"appropriate"}),
                "avg_top5_usefulness": _average_numeric(method_rows, "top5_usefulness"),
            }
        )

    destination_root.mkdir(parents=True, exist_ok=True)
    csv_path = destination_root / "annotation-summary.csv"
    json_path = destination_root / "annotation-summary.json"
    latex_path = destination_root / "annotation-summary.tex"
    _write_csv(summary_rows, csv_path)
    json_path.write_text(json.dumps(summary_rows, indent=2), encoding="utf-8")
    latex_path.write_text(_to_latex_table(summary_rows), encoding="utf-8")
    return csv_path, json_path


def _read_rows(path: Path):
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            yield {key: (value or "").strip() for key, value in row.items()}


def _fraction(rows: list[dict[str, str]], field: str, accepted: set[str]) -> float:
    labeled = [row for row in rows if row.get(field)]
    if not labeled:
        return 0.0
    matched = sum(1 for row in labeled if row.get(field) in accepted)
    return round(matched / len(labeled), 4)


def _average_numeric(rows: list[dict[str, str]], field: str) -> float:
    values: list[float] = []
    for row in rows:
        raw = row.get(field, "")
        if not raw:
            continue
        try:
            values.append(float(raw))
        except ValueError:
            continue
    if not values:
        return 0.0
    return round(sum(values) / len(values), 4)


def _write_csv(rows: list[dict[str, str | float | int]], destination: Path) -> None:
    with destination.open("w", encoding="utf-8", newline="") as handle:
        if not rows:
            writer = csv.writer(handle)
            writer.writerow(
                [
                    "method",
                    "findings",
                    "precision",
                    "partial_or_better",
                    "actionable_rate",
                    "actionable_or_better",
                    "duplicate_rate",
                    "severity_agreement",
                    "avg_top5_usefulness",
                ]
            )
            return
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _to_latex_table(rows: list[dict[str, str | float | int]]) -> str:
    header = (
        "\\begin{tabular}{lrrrrrrr}\n"
        "\\toprule\n"
        "Method & Findings & Precision & Partial+ & Actionable & Actionable+ & Duplicate & Severity Agree. \\\\\n"
        "\\midrule\n"
    )
    body_lines: list[str] = []
    for row in rows:
        method = str(row["method"]).replace("_", "\\_")
        body_lines.append(
            f"{method} & {row['findings']} & {row['precision']:.4f} & {row['partial_or_better']:.4f} & "
            f"{row['actionable_rate']:.4f} & {row['actionable_or_better']:.4f} & {row['duplicate_rate']:.4f} & "
            f"{row['severity_agreement']:.4f} \\\\"
        )
    footer = "\n\\bottomrule\n\\end{tabular}\n"
    return header + "\n".join(body_lines) + footer
