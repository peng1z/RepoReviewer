from pathlib import Path

from repo_reviewer.repository import prioritize_files, should_ignore


def test_should_ignore_matches_glob() -> None:
    assert should_ignore(Path("node_modules/react/index.js"), ["node_modules/**"])


def test_prioritize_files_prefers_source_files() -> None:
    files = [Path("README.md"), Path("docs/guide.md"), Path("src/main.py"), Path("package.json")]
    prioritized = prioritize_files(files, 2)
    assert Path("README.md") in prioritized
    assert Path("src/main.py") in prioritized
