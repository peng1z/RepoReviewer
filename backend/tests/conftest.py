from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Callable

import pytest

from repo_reviewer.prompts import CONTEXT_SYSTEM_PROMPT, REVIEW_SYSTEM_PROMPT, SUMMARY_SYSTEM_PROMPT


class FakeLLM:
    """Stands in for structured_completion, routed by system prompt.

    Each agent uses a distinct system prompt, so a test can script the context,
    review and summary responses independently. Values may be strings, dicts
    (serialised to JSON) or callables receiving the user prompt.
    """

    def __init__(self) -> None:
        self.responses: dict[str, object] = {}
        self.calls: list[dict] = []

    def on_context(self, response) -> "FakeLLM":
        self.responses[CONTEXT_SYSTEM_PROMPT] = response
        return self

    def on_review(self, response) -> "FakeLLM":
        self.responses[REVIEW_SYSTEM_PROMPT] = response
        return self

    def on_summary(self, response) -> "FakeLLM":
        self.responses[SUMMARY_SYSTEM_PROMPT] = response
        return self

    async def __call__(self, *, provider, model, system_prompt, user_prompt) -> str:
        self.calls.append({"system": system_prompt, "user": user_prompt, "model": model})
        response = self.responses.get(system_prompt, "[]")
        if callable(response):
            response = response(user_prompt)
        if isinstance(response, (dict, list)):
            return json.dumps(response)
        return str(response)


@pytest.fixture
def fake_llm(monkeypatch) -> FakeLLM:
    llm = FakeLLM()
    # Each module binds the name at import, so patch where it is looked up.
    monkeypatch.setattr("repo_reviewer.workflow.structured_completion", llm)
    monkeypatch.setattr("repo_reviewer.evaluation.structured_completion", llm)
    return llm


SAMPLE_SOURCE = '''def clamp(value, low, high):
    if value is None:
        return low
    if value <= low:
        return low
    return value
'''


@pytest.fixture
def sample_repo(tmp_path: Path) -> Path:
    """A small repository on disk, the shape context_agent expects."""
    root = tmp_path / "repo"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "core.py").write_text(SAMPLE_SOURCE, encoding="utf-8")
    (root / "pkg" / "util.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
    (root / "README.md").write_text("# Demo\n\nA demo project.\n", encoding="utf-8")
    (root / "pyproject.toml").write_text('[project]\nname = "demo"\n', encoding="utf-8")
    (root / "tests").mkdir()
    (root / "tests" / "test_core.py").write_text("def test():\n    pass\n", encoding="utf-8")
    (root / "logo.png").write_bytes(b"\x89PNG\r\n")
    return root


@pytest.fixture
def local_clone(sample_repo, monkeypatch, tmp_path):
    """Make cloner_agent 'clone' the on-disk sample repo instead of GitHub."""
    def fake_clone(url: str, destination: Path) -> Path:
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(sample_repo, destination)
        return destination

    monkeypatch.setattr("repo_reviewer.workflow.clone_repo", fake_clone)
    monkeypatch.chdir(tmp_path)  # cloner writes under ./.cache
