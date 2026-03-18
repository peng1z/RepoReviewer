from pathlib import Path

from repo_reviewer.evaluation import (
    EvaluationDataset,
    EvaluationRepo,
    export_annotation_sheet,
    export_rows,
    load_dataset,
)


def test_load_dataset(tmp_path: Path) -> None:
    path = tmp_path / "dataset.json"
    path.write_text(
        '{"name":"sample","provider":"groq","model":"groq/llama-3.3-70b-versatile","repos":[{"github_url":"https://github.com/octocat/Hello-World"}]}',
        encoding="utf-8",
    )
    dataset = load_dataset(path)
    assert dataset.name == "sample"
    assert dataset.repos[0].github_url == "https://github.com/octocat/Hello-World"


def test_export_rows_writes_csv(tmp_path: Path) -> None:
    dataset = EvaluationDataset(
        name="sample",
        repos=[EvaluationRepo(github_url="https://github.com/octocat/Hello-World")],
    )
    rows = []
    destination = tmp_path / "results.csv"
    export_rows(rows, destination)
    assert destination.exists()


def test_export_annotation_sheet_writes_rows(tmp_path: Path) -> None:
    experiment_dir = tmp_path / "experiment"
    runs_dir = experiment_dir / "runs"
    runs_dir.mkdir(parents=True)
    (runs_dir / "sample.json").write_text(
        '{"row":{"run_id":"r1","repo_name":"demo","method":"full","provider":"groq","model":"m"},"result":{"comments":[{"file":"a.py","line":1,"severity":"low","issue":"i","suggestion":"s","snippet":null}]}}',
        encoding="utf-8",
    )
    destination = export_annotation_sheet(experiment_dir)
    assert destination.exists()
    content = destination.read_text(encoding="utf-8")
    assert "a.py" in content
