from __future__ import annotations

import asyncio
import json
import os
import random
import re
import time
from collections import deque

import litellm
from litellm import acompletion

from .models import ReviewComment


#: Errors worth another attempt: transient server, network and quota faults.
#: Anything else (bad request, auth, unknown model) is a permanent failure and
#: retrying it would only waste quota.
RETRYABLE_ERRORS = (
    litellm.RateLimitError,
    litellm.APIConnectionError,
    litellm.ServiceUnavailableError,
    litellm.InternalServerError,
    litellm.Timeout,
)


class RateLimiter:
    """Allows at most `max_calls` acquisitions per rolling `period` seconds."""

    def __init__(self, max_calls: int, period: float = 60.0) -> None:
        self.max_calls = max_calls
        self.period = period
        self._calls: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        if self.max_calls <= 0:
            return
        async with self._lock:
            while True:
                now = time.monotonic()
                while self._calls and now - self._calls[0] >= self.period:
                    self._calls.popleft()
                if len(self._calls) < self.max_calls:
                    self._calls.append(now)
                    return
                await asyncio.sleep(self.period - (now - self._calls[0]) + 0.01)


_limiter: RateLimiter | None = None
_max_retries: int | None = None
_retry_base: float | None = None


def configure_llm(
    *,
    requests_per_minute: int | None = None,
    max_retries: int | None = None,
    retry_base_seconds: float | None = None,
) -> None:
    """Override the pacing and retry policy (used by the benchmark and tests)."""
    global _limiter, _max_retries, _retry_base
    if requests_per_minute is not None:
        _limiter = RateLimiter(requests_per_minute)
    if max_retries is not None:
        _max_retries = max_retries
    if retry_base_seconds is not None:
        _retry_base = retry_base_seconds


def _policy() -> tuple[RateLimiter, int, float]:
    global _limiter, _max_retries, _retry_base
    if _limiter is None or _max_retries is None or _retry_base is None:
        from .config import Settings

        settings = Settings()
        if _limiter is None:
            _limiter = RateLimiter(settings.llm_requests_per_minute)
        if _max_retries is None:
            _max_retries = settings.llm_max_retries
        if _retry_base is None:
            _retry_base = settings.llm_retry_base_seconds
    return _limiter, _max_retries, _retry_base


MODEL_ALIASES = {
    "openai": "openai/gpt-4.1-mini",
    "anthropic": "anthropic/claude-3-5-haiku-latest",
    "openrouter": "openrouter/openai/gpt-4.1-mini",
    "groq": "groq/llama-3.3-70b-versatile",
}


def resolve_model(provider: str, model: str | None) -> str:
    if model:
        if "/" in model:
            return model
        return f"{provider}/{model}"
    return MODEL_ALIASES.get(provider, "openai/gpt-4.1-mini")


async def structured_completion(
    *,
    provider: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
) -> str:
    resolved_model = resolve_model(provider, model)
    limiter, max_retries, retry_base = _policy()
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    for attempt in range(max_retries + 1):
        await limiter.acquire()
        try:
            response = await acompletion(
                model=resolved_model,
                messages=messages,
                temperature=0.2,
            )
        except RETRYABLE_ERRORS:
            if attempt == max_retries:
                raise
            # Exponential backoff with jitter, so parallel callers do not line
            # up and hit the same limit again in lockstep.
            delay = retry_base * (2**attempt)
            await asyncio.sleep(delay + random.uniform(0, retry_base))
            continue
        return response.choices[0].message.content or ""

    raise AssertionError("unreachable")  # pragma: no cover


def env_hint_for_provider(provider: str) -> str:
    mapping = {
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
        "groq": "GROQ_API_KEY",
    }
    env_name = mapping.get(provider, "API_KEY")
    if os.getenv(env_name):
        return env_name
    return env_name


def normalize_comments(comments: list[ReviewComment]) -> list[ReviewComment]:
    seen: set[tuple[str, int | None, str]] = set()
    deduped: list[ReviewComment] = []
    for comment in comments:
        key = (comment.file, comment.line, comment.issue.strip().lower())
        if key in seen:
            continue
        seen.add(key)
        deduped.append(comment)
    severity_rank = {"high": 0, "medium": 1, "low": 2}
    return sorted(deduped, key=lambda item: (severity_rank[item.severity], item.file, item.line or 0))


def extract_json_payload(text: str) -> str:
    stripped = text.strip()
    if not stripped:
        raise ValueError("Empty LLM response")
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    if stripped[0] in "[{":
        return stripped
    start_candidates = [index for index in (stripped.find("{"), stripped.find("[")) if index != -1]
    if not start_candidates:
        raise ValueError("No JSON payload found")
    start = min(start_candidates)
    end_object = stripped.rfind("}")
    end_array = stripped.rfind("]")
    end = max(end_object, end_array)
    if end < start:
        raise ValueError("No complete JSON payload found")
    return stripped[start : end + 1]


def parse_json_response(text: str):
    payload = extract_json_payload(text)
    return json.loads(payload)
