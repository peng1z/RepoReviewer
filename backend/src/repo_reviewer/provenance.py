"""Where a report came from.

The commit is read from the checkout the tool is running out of, when there is
one. An installed copy has no checkout and reports None rather than inventing a
value: an unknown commit is a fact about the report, and a wrong one is worse
than an absent one.
"""

from __future__ import annotations

import subprocess
from functools import lru_cache
from importlib.metadata import PackageNotFoundError, version as package_version
from pathlib import Path

from .models import ToolProvenance


@lru_cache(maxsize=1)
def _version() -> str:
    try:
        return package_version("repo-reviewer")
    except PackageNotFoundError:
        return "unknown"


@lru_cache(maxsize=1)
def _commit() -> str | None:
    root = Path(__file__).resolve().parents[3]
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip() or None


def tool_provenance() -> ToolProvenance:
    return ToolProvenance(version=_version(), commit=_commit())
