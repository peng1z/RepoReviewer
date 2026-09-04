from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from repo_reviewer.evaluation import EvaluationDataset, EvaluationRepo, run_evaluation_dataset, run_evaluation_sync


DUPLICATE_FINDINGS = [
    {"file": "pkg/core.py", "line": 4, "severity": "high", "issue": "boundary", "suggestion": "use <"},
    {"file": "pkg/core.py", "line": 4, "severity": "high", "issue": "boundary", "suggestion": "use <"},
]


def _dataset(methods=None) -> EvaluationDataset:
    return EvaluationDataset(
        name="e2e", provider="openai", model="openai/x",
        methods=methods or ["full", "single_agent", "no_context", "no_priority"],
        repos=[EvaluationRepo(github_url="https://github.com/o/demo", max_files=1, include_tests=False)],
    )


def _script(fake_llm) -> None:
    fake_llm.on_context({"readme_summary": "r", "folder_summary": "f", "architecture_notes": []})
    fake_llm.on_review(DUPLICATE_FINDINGS)
    fake_llm.on_summary({"headline": "h", "top_findings": ["boundary"]})


@pytest.mark.asyncio
async def test_every_method_runs_and_leaves_its_artifacts(local_clone, fake_llm, tmp_path) -> None:
    _script(fake_llm)
    experiment_dir, rows = await run_evaluation_dataset(_dataset(), output_root=tmp_path / "exp")

    assert {row.method for row in rows} == {"full", "single_agent", "no_context", "no_priority"}
    assert all(row.repo_name == "demo" and row.runtime_seconds >= 0 for row in rows)
    for row in rows:
        assert (Path(row.artifact_dir) / "review.json").exists(), row.method
        assert (Path(row.artifact_dir) / "review.md").exists(), row.method

    with (experiment_dir / "results.csv").open(encoding="utf-8") as handle:
        assert len(list(csv.DictReader(handle))) == 4
    assert (experiment_dir / "dataset.json").exists()
    assert len(list((experiment_dir / "runs").glob("*.json"))) == 4


@pytest.mark.asyncio
async def test_annotation_sheet_has_one_row_per_finding(local_clone, fake_llm, tmp_path) -> None:
    _script(fake_llm)
    experiment_dir, rows = await run_evaluation_dataset(_dataset(["full"]), output_root=tmp_path / "exp")
    with (experiment_dir / "annotation-sheet.csv").open(encoding="utf-8") as handle:
        sheet = list(csv.DictReader(handle))
    assert len(sheet) == rows[0].total_findings
    assert sheet[0]["method"] == "full" and sheet[0]["correctness"] == ""


@pytest.mark.asyncio
async def test_no_context_really_withholds_the_context(local_clone, fake_llm, tmp_path) -> None:
    _script(fake_llm)
    _, rows = await run_evaluation_dataset(_dataset(["no_context"]), output_root=tmp_path / "exp")
    saved = json.loads((Path(rows[0].artifact_dir) / "review.json").read_text(encoding="utf-8"))
    assert "disabled for ablation" in saved["context"]["readme_summary"]
    review_prompts = [c["user"] for c in fake_llm.calls if "Review this file" in c["user"]]
    assert review_prompts and all("disabled for ablation" in p for p in review_prompts)


@pytest.mark.asyncio
async def test_no_priority_is_the_only_method_that_keeps_duplicates(local_clone, fake_llm, tmp_path) -> None:
    """The ablation's operational definition: PriorityAgent dedupes and ranks,
    so disabling it must leave the raw review output untouched."""
    _script(fake_llm)
    _, rows = await run_evaluation_dataset(_dataset(), output_root=tmp_path / "exp")
    findings = {row.method: row.total_findings for row in rows}
    assert findings["full"] == 1
    assert findings["single_agent"] == 1
    assert findings["no_context"] == 1
    assert findings["no_priority"] == 2, findings


def test_run_evaluation_sync_reads_a_dataset_file(local_clone, fake_llm, tmp_path) -> None:
    _script(fake_llm)
    path = tmp_path / "ds.json"
    path.write_text(json.dumps(_dataset(["full"]).model_dump(mode="json")), encoding="utf-8")
    experiment_dir, rows = run_evaluation_sync(path, tmp_path / "exp")
    assert len(rows) == 1 and experiment_dir.name.startswith("e2e-")
