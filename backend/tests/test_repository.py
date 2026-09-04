from pathlib import Path

from repo_reviewer.repository import prioritize_files, should_ignore


def test_should_ignore_matches_glob() -> None:
    assert should_ignore(Path("node_modules/react/index.js"), ["node_modules/**"])


def test_prioritize_files_prefers_source_files() -> None:
    files = [Path("README.md"), Path("docs/guide.md"), Path("src/main.py"), Path("package.json")]
    prioritized = prioritize_files(files, 2)
    assert Path("README.md") in prioritized
    assert Path("src/main.py") in prioritized


# --- collection, prioritisation and snippets ----------------------------------

from pathlib import Path

import pytest

from repo_reviewer.repository import (
    collect_repo_files, detect_key_files, extract_snippet, is_probably_text_file, prioritize_files,
)


@pytest.mark.parametrize(("name", "expected"), [
    ("a.py", True), ("Dockerfile", True), (".env", True), ("README", True),
    ("logo.png", False), ("model.bin", False), ("x.PY", True),
])
def test_text_file_detection(name, expected) -> None:
    assert is_probably_text_file(Path(name)) is expected


def test_collect_reports_a_reason_for_every_skip(tmp_path) -> None:
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "dep.js").write_text("x", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_a.py").write_text("x", encoding="utf-8")
    (tmp_path / "img.png").write_bytes(b"\x89PNG")
    (tmp_path / "huge.py").write_text("x" * 100, encoding="utf-8")
    (tmp_path / "ok.py").write_text("x", encoding="utf-8")

    accepted, skipped = collect_repo_files(
        tmp_path, ignore_globs=["node_modules/**"], include_tests=False, max_file_bytes=50,
    )
    reasons = {p.as_posix(): r for p, r in skipped}
    assert [p.as_posix() for p in accepted] == ["ok.py"]
    assert reasons["node_modules/dep.js"] == "ignored pattern"
    assert reasons["tests/test_a.py"] == "test file"
    assert reasons["img.png"] == "binary or unsupported file type"
    assert reasons["huge.py"].startswith("file too large")


def test_prioritise_puts_readme_then_source_then_config(tmp_path) -> None:
    files = [Path(n) for n in ("setup.cfg", "pkg/deep/mod.py", "README.md", "package.json", "notes.md", "app.py")]
    ordered = [p.as_posix() for p in prioritize_files(files, max_files=10)]
    assert ordered[0] == "README.md"
    assert ordered[1:3] == ["app.py", "pkg/deep/mod.py"]      # source, shallow first
    assert ordered.index("package.json") < ordered.index("notes.md")


def test_prioritise_truncates_to_max_files() -> None:
    files = [Path(f"m{i}.py") for i in range(10)]
    assert len(prioritize_files(files, max_files=3)) == 3


def test_snippet_is_centred_on_the_line_and_numbered(tmp_path) -> None:
    path = tmp_path / "f.py"
    path.write_text("\n".join(f"line{i}" for i in range(1, 21)), encoding="utf-8")
    snippet = extract_snippet(path, line=10, radius=2)
    assert snippet.splitlines() == ["8: line8", "9: line9", "10: line10", "11: line11", "12: line12"]


def test_snippet_without_a_line_shows_the_head(tmp_path) -> None:
    path = tmp_path / "f.py"
    path.write_text("\n".join(f"l{i}" for i in range(1, 21)), encoding="utf-8")
    assert extract_snippet(path, line=None, radius=2).splitlines() == ["1: l1", "2: l2", "3: l3", "4: l4"]


def test_snippet_clamps_at_file_edges(tmp_path) -> None:
    path = tmp_path / "f.py"
    path.write_text("a\nb\nc", encoding="utf-8")
    assert extract_snippet(path, line=1, radius=5).splitlines() == ["1: a", "2: b", "3: c"]


def test_snippet_of_an_empty_file_is_empty(tmp_path) -> None:
    path = tmp_path / "f.py"
    path.write_text("", encoding="utf-8")
    assert extract_snippet(path, line=1, radius=3) == ""


def test_key_files_put_the_readme_first_and_cap_at_eight(tmp_path) -> None:
    (tmp_path / "README.md").write_text("#", encoding="utf-8")
    files = [Path(n) for n in ("pyproject.toml", "package.json", "Dockerfile", "docker-compose.yml",
                               "requirements.txt", "README", "a.py", "b.py", "c.py")]
    keys = detect_key_files(tmp_path, files)
    assert keys[0] == "README.md"
    assert "a.py" not in keys
    assert len(keys) <= 8
