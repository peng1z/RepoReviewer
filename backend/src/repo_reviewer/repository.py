from __future__ import annotations

from fnmatch import fnmatch
from pathlib import Path


TEXT_EXTENSIONS = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".json", ".md", ".yml", ".yaml", ".toml", ".ini", ".sh",
    ".go", ".rs", ".java", ".kt", ".rb", ".php", ".css", ".scss", ".html", ".sql", ".env", ".txt",
}

KEY_FILE_NAMES = {
    "README.md",
    "README",
    "pyproject.toml",
    "package.json",
    "requirements.txt",
    "Dockerfile",
    "docker-compose.yml",
    ".github/workflows/ci.yml",
}


def should_ignore(path: Path, ignore_globs: list[str]) -> bool:
    path_str = path.as_posix()
    return any(fnmatch(path_str, pattern) for pattern in ignore_globs)


def is_probably_text_file(path: Path) -> bool:
    return path.suffix.lower() in TEXT_EXTENSIONS or path.name in {".env", "Dockerfile", "README"}


def collect_repo_files(
    repo_root: Path,
    *,
    ignore_globs: list[str],
    include_tests: bool,
    max_file_bytes: int,
) -> tuple[list[Path], list[tuple[Path, str]]]:
    accepted: list[Path] = []
    skipped: list[tuple[Path, str]] = []
    for path in sorted(repo_root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(repo_root)
        if should_ignore(relative, ignore_globs):
            skipped.append((relative, "ignored pattern"))
            continue
        if not include_tests and ("test" in relative.parts or "tests" in relative.parts):
            skipped.append((relative, "test file"))
            continue
        if not is_probably_text_file(path):
            skipped.append((relative, "binary or unsupported file type"))
            continue
        size = path.stat().st_size
        if size > max_file_bytes:
            skipped.append((relative, f"file too large ({size} bytes)"))
            continue
        accepted.append(relative)
    return accepted, skipped


def prioritize_files(files: list[Path], max_files: int) -> tuple[list[Path], list[Path]]:
    """Rank reviewable files and split them at the cap.

    Returns (selected, dropped). The second half exists because it used to be
    thrown away: the function ended in `sorted(...)[:max_files]` and the files
    past the cut vanished without a record, while files the collector rejected
    each carried a reason. A review of psf/requests reported 109 skipped files
    with reasons and said nothing about the 40 eligible ones it never opened --
    so the report read as complete coverage of 17%.
    """
    def score(path: Path) -> tuple[int, int, str]:
        name = path.name
        suffix = path.suffix.lower()
        path_str = path.as_posix()
        primary = 0
        if name == "README.md":
            primary = -4
        elif suffix in {".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs"}:
            primary = -3
        elif name in KEY_FILE_NAMES:
            primary = -2
        elif suffix in {".toml", ".json", ".yml", ".yaml", ".md"}:
            primary = -1
        depth = len(path.parts)
        return (primary, depth, path_str)

    ranked = sorted(files, key=score)
    return ranked[:max_files], ranked[max_files:]


def extract_snippet(path: Path, line: int | None, radius: int) -> str:
    text = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    if not text:
        return ""
    if line is None:
        start = 0
        end = min(len(text), radius * 2)
    else:
        start = max(0, line - radius - 1)
        end = min(len(text), line + radius)
    numbered = [f"{idx + 1}: {text[idx]}" for idx in range(start, end)]
    return "\n".join(numbered)


def detect_key_files(repo_root: Path, files: list[Path]) -> list[str]:
    key_files = [path.as_posix() for path in files if path.name in KEY_FILE_NAMES]
    if (repo_root / "README.md").exists() and "README.md" not in key_files:
        key_files.insert(0, "README.md")
    if (repo_root / "README").exists() and "README" not in key_files:
        key_files.insert(0, "README")
    return key_files[:8]
