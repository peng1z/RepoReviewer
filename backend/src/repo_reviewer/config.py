from __future__ import annotations

from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore[no-redef]

from pydantic_settings import BaseSettings, SettingsConfigDict

from .models import ResolvedConfig


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "outputs"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "development"
    default_provider: str = "openai"
    default_model: str = "gpt-4.1-mini"
    github_token: str | None = None
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    openrouter_api_key: str | None = None
    groq_api_key: str | None = None
    host: str = "127.0.0.1"
    port: int = 8000
    # OpenRouter allows 20 requests/minute on free models; pacing locally is
    # cheaper than absorbing 429s and burning the daily quota on retries.
    llm_requests_per_minute: int = 20
    llm_max_retries: int = 5
    llm_retry_base_seconds: float = 2.0


DEFAULT_REVIEWER_CONFIG: dict[str, Any] = {
    "ignore_globs": [
        ".git/**",
        "node_modules/**",
        ".next/**",
        "dist/**",
        "build/**",
        "coverage/**",
        "vendor/**",
        "*.lock",
        "*.png",
        "*.jpg",
        "*.jpeg",
        "*.gif",
        "*.ico",
        "*.pdf",
        "*.zip",
        "*.tar",
        "*.gz",
        "*.min.js",
        "*.min.css",
    ],
    "max_files": 30,
    "max_file_bytes": 40_000,
    "include_tests": True,
    "snippet_radius": 6,
}


def load_repo_config(repo_root: Path) -> ResolvedConfig:
    config_path = repo_root / ".repo-reviewer.toml"
    if not config_path.exists():
        return ResolvedConfig(path=config_path, data=DEFAULT_REVIEWER_CONFIG.copy())

    with config_path.open("rb") as handle:
        parsed = tomllib.load(handle)

    data = DEFAULT_REVIEWER_CONFIG.copy()
    data.update(parsed)
    return ResolvedConfig(path=config_path, data=data)
