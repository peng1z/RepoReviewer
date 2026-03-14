from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import shutil
import subprocess

from github import Github


GITHUB_URL_RE = re.compile(r"https://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$")


@dataclass(slots=True)
class ParsedGitHubUrl:
    owner: str
    repo: str

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.repo}"


def parse_github_url(url: str) -> ParsedGitHubUrl:
    match = GITHUB_URL_RE.match(url)
    if not match:
        raise ValueError(f"Unsupported GitHub URL: {url}")
    return ParsedGitHubUrl(owner=match.group("owner"), repo=match.group("repo"))


def clone_repo(url: str, destination: Path) -> Path:
    if destination.exists():
        shutil.rmtree(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "clone", "--depth", "1", url, str(destination)],
        check=True,
        capture_output=True,
        text=True,
    )
    return destination


def checkout_pr_head(url: str, destination: Path, pr_number: int, github_token: str | None) -> Path:
    parsed = parse_github_url(url)
    client = Github(github_token) if github_token else Github()
    repo = client.get_repo(parsed.full_name)
    pull_request = repo.get_pull(pr_number)
    head_ref = pull_request.head.ref
    head_repo_clone_url = pull_request.head.repo.clone_url
    remote_name = "pr-head"
    existing_remotes = subprocess.run(
        ["git", "-C", str(destination), "remote"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    if remote_name in existing_remotes:
        subprocess.run(
            ["git", "-C", str(destination), "remote", "remove", remote_name],
            check=True,
            capture_output=True,
            text=True,
        )
    subprocess.run(
        ["git", "-C", str(destination), "remote", "add", remote_name, head_repo_clone_url],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "-C", str(destination), "fetch", remote_name, f"{head_ref}:{head_ref}"],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "-C", str(destination), "checkout", head_ref],
        check=True,
        capture_output=True,
        text=True,
    )
    return destination


def fetch_pr_changed_files(url: str, pr_number: int, github_token: str | None) -> list[str]:
    parsed = parse_github_url(url)
    client = Github(github_token) if github_token else Github()
    repo = client.get_repo(parsed.full_name)
    pull_request = repo.get_pull(pr_number)
    return [file.filename for file in pull_request.get_files()]
