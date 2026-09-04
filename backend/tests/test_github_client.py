from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from repo_reviewer.github_client import clone_repo, parse_github_url


@pytest.mark.parametrize(
    ("url", "owner", "repo"),
    [("https://github.com/psf/requests", "psf", "requests"),
     ("https://github.com/psf/requests.git", "psf", "requests"),
     ("https://github.com/psf/requests/", "psf", "requests")],
)
def test_github_urls_are_parsed(url, owner, repo) -> None:
    parsed = parse_github_url(url)
    assert (parsed.owner, parsed.repo, parsed.full_name) == (owner, repo, f"{owner}/{repo}")


@pytest.mark.parametrize("url", ["https://gitlab.com/a/b", "github.com/a/b", "https://github.com/onlyowner", ""])
def test_non_github_urls_are_rejected(url) -> None:
    with pytest.raises(ValueError, match="Unsupported GitHub URL"):
        parse_github_url(url)


@pytest.fixture
def local_git_repo(tmp_path) -> Path:
    """A real git repository, so clone_repo is exercised against git itself."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "hello.py").write_text("print('hi')\n", encoding="utf-8")
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@x"}
    for cmd in (["git", "init", "-q"], ["git", "add", "."], ["git", "commit", "-qm", "init"]):
        subprocess.run(cmd, cwd=src, check=True, env={**__import__("os").environ, **env})
    return src


def test_clone_produces_a_working_copy(local_git_repo, tmp_path) -> None:
    dest = tmp_path / "clone"
    clone_repo(str(local_git_repo), dest)
    assert (dest / "hello.py").read_text(encoding="utf-8") == "print('hi')\n"


def test_clone_replaces_an_existing_destination(local_git_repo, tmp_path) -> None:
    """A stale checkout from a previous run must not survive into the next."""
    dest = tmp_path / "clone"
    dest.mkdir()
    (dest / "stale.txt").write_text("old", encoding="utf-8")
    clone_repo(str(local_git_repo), dest)
    assert not (dest / "stale.txt").exists()
    assert (dest / "hello.py").exists()


def test_clone_failure_surfaces_as_an_error(tmp_path) -> None:
    with pytest.raises(subprocess.CalledProcessError):
        clone_repo(str(tmp_path / "does-not-exist"), tmp_path / "clone")


# --- PR helpers: real git, fake GitHub API ----------------------------------

from types import SimpleNamespace

from repo_reviewer.github_client import checkout_pr_head, fetch_pr_changed_files


def _git(cwd: Path, *args: str) -> str:
    import os
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@x"}
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True,
                          text=True, env=env).stdout


@pytest.fixture
def fork_with_branch(local_git_repo, tmp_path) -> tuple[Path, str]:
    """A 'fork' of the base repo carrying a feature branch, like a PR head."""
    fork = tmp_path / "fork"
    subprocess.run(["git", "clone", "-q", str(local_git_repo), str(fork)], check=True)
    _git(fork, "checkout", "-qb", "feature")
    (fork / "feature.py").write_text("x = 1\n", encoding="utf-8")
    _git(fork, "add", ".")
    _git(fork, "commit", "-qm", "feature work")
    return fork, "feature"


def _fake_github(monkeypatch, *, head_ref=None, clone_url=None, files=()):
    """Replace PyGithub's Github with an object shaped like the calls we make."""
    captured: dict = {}

    class FakePR:
        head = SimpleNamespace(ref=head_ref, repo=SimpleNamespace(clone_url=clone_url))

        def get_files(self):
            return [SimpleNamespace(filename=f) for f in files]

    class FakeRepo:
        def get_pull(self, number):
            captured["pr_number"] = number
            return FakePR()

    class FakeGithub:
        def __init__(self, token=None):
            captured["token"] = token

        def get_repo(self, full_name):
            captured["full_name"] = full_name
            return FakeRepo()

    monkeypatch.setattr("repo_reviewer.github_client.Github", FakeGithub)
    return captured


def test_checkout_pr_head_fetches_and_checks_out_the_fork_branch(local_git_repo, fork_with_branch, tmp_path, monkeypatch) -> None:
    fork, branch = fork_with_branch
    dest = tmp_path / "work"
    clone_repo(str(local_git_repo), dest)
    assert not (dest / "feature.py").exists()

    captured = _fake_github(monkeypatch, head_ref=branch, clone_url=str(fork))
    checkout_pr_head("https://github.com/o/demo", dest, 17, "tok")

    assert (dest / "feature.py").exists()                       # the PR head is checked out
    assert _git(dest, "rev-parse", "--abbrev-ref", "HEAD").strip() == branch
    assert captured == {"token": "tok", "full_name": "o/demo", "pr_number": 17}


def test_checkout_pr_head_is_idempotent_over_the_remote(local_git_repo, fork_with_branch, tmp_path, monkeypatch) -> None:
    """A second checkout must replace the stale pr-head remote, not fail on it."""
    fork, branch = fork_with_branch
    dest = tmp_path / "work"
    clone_repo(str(local_git_repo), dest)
    _fake_github(monkeypatch, head_ref=branch, clone_url=str(fork))

    checkout_pr_head("https://github.com/o/demo", dest, 1, None)
    _git(dest, "checkout", "-q", "-")   # go back so the branch can be re-fetched
    checkout_pr_head("https://github.com/o/demo", dest, 1, None)

    remotes = _git(dest, "remote").split()
    assert remotes.count("pr-head") == 1


def test_checkout_without_a_token_uses_anonymous_access(local_git_repo, fork_with_branch, tmp_path, monkeypatch) -> None:
    fork, branch = fork_with_branch
    dest = tmp_path / "work"
    clone_repo(str(local_git_repo), dest)
    captured = _fake_github(monkeypatch, head_ref=branch, clone_url=str(fork))
    checkout_pr_head("https://github.com/o/demo", dest, 1, None)
    assert captured["token"] is None


def test_fetch_pr_changed_files_returns_filenames(monkeypatch) -> None:
    captured = _fake_github(monkeypatch, files=["src/a.py", "docs/b.md"])
    assert fetch_pr_changed_files("https://github.com/o/demo", 9, "tok") == ["src/a.py", "docs/b.md"]
    assert captured["pr_number"] == 9 and captured["full_name"] == "o/demo"
