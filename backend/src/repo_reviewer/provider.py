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
from pydantic import ValidationError

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


#: Never sleep longer than this on a provider's say-so.
MAX_RETRY_SLEEP_SECONDS = 90.0


def retry_after_seconds(exc: Exception) -> float | None:
    """Read the provider's own backoff hint, if it sent one.

    OpenRouter returns `Retry-After` and a `retry_after_seconds` field on
    upstream pool exhaustion. Obeying it beats guessing.
    """
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers:
        raw = headers.get("retry-after") or headers.get("Retry-After")
        if raw:
            try:
                return max(0.0, float(raw))
            except (TypeError, ValueError):
                pass
    match = re.search(r'"retry_after_seconds"\s*:\s*([0-9.]+)', str(exc))
    if match:
        try:
            return max(0.0, float(match.group(1)))
        except ValueError:
            pass
    return None


_limiter: RateLimiter | None = None
_max_retries: int | None = None
_retry_base: float | None = None
_timeout: float | None = None


def configure_llm(
    *,
    requests_per_minute: int | None = None,
    max_retries: int | None = None,
    retry_base_seconds: float | None = None,
    request_timeout_seconds: float | None = None,
) -> None:
    """Override the pacing and retry policy (used by the benchmark and tests)."""
    global _limiter, _max_retries, _retry_base, _timeout
    if requests_per_minute is not None:
        _limiter = RateLimiter(requests_per_minute)
    if max_retries is not None:
        _max_retries = max_retries
    if retry_base_seconds is not None:
        _retry_base = retry_base_seconds
    if request_timeout_seconds is not None:
        _timeout = request_timeout_seconds


def _policy() -> tuple[RateLimiter, int, float, float]:
    global _limiter, _max_retries, _retry_base, _timeout
    if None in (_limiter, _max_retries, _retry_base, _timeout):
        from .config import Settings

        settings = Settings()
        if _limiter is None:
            _limiter = RateLimiter(settings.llm_requests_per_minute)
        if _max_retries is None:
            _max_retries = settings.llm_max_retries
        if _retry_base is None:
            _retry_base = settings.llm_retry_base_seconds
        if _timeout is None:
            _timeout = settings.llm_request_timeout_seconds
    return _limiter, _max_retries, _retry_base, _timeout


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
    limiter, max_retries, retry_base, timeout = _policy()
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
                timeout=timeout,
            )
        except RETRYABLE_ERRORS as exc:
            if attempt == max_retries:
                raise
            # Exponential backoff with jitter, so parallel callers do not line
            # up and hit the same limit again in lockstep. If the provider told
            # us how long to wait, never wait less than that.
            delay = retry_base * (2**attempt)
            hint = retry_after_seconds(exc)
            if hint is not None:
                delay = max(delay, hint)
            delay = min(delay, MAX_RETRY_SLEEP_SECONDS)
            await asyncio.sleep(delay + random.uniform(0, retry_base))
            continue
        return response.choices[0].message.content or ""

    raise AssertionError("unreachable")  # pragma: no cover


PROVIDER_ENV_VARS = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "groq": "GROQ_API_KEY",
}


def env_var_for_provider(provider: str) -> str:
    """The environment variable litellm reads this provider's key from."""
    return PROVIDER_ENV_VARS.get(provider, "API_KEY")


def missing_api_key(provider: str) -> str | None:
    """The variable the user still needs to set, or None if it already holds one.

    The previous version of this check returned the same name from both
    branches of its `if`, so the CLI told everyone to set a key they had
    already provided. backend/.env counts: the package loads it on import.
    """
    name = env_var_for_provider(provider)
    return None if os.getenv(name) else name


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


def coerce_comment_payload(payload) -> list[dict]:
    """Normalise the shapes models actually return into a list of finding dicts.

    A bare list is the documented contract, but models routinely wrap it in an
    object (``{"comments": [...]}``) or return a single finding on its own.
    Iterating a wrapper dict yields its keys, so the caller would try to
    validate the string "comments" as a ReviewComment.
    """
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        if "file" in payload and ("issue" in payload or "suggestion" in payload):
            return [payload]
        for value in payload.values():
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def build_comments(
    items: list[dict],
    *,
    default_file: str | None = None,
) -> tuple[list[ReviewComment], list[str]]:
    """Validate findings one at a time, returning the good ones and why the rest went.

    Validating the batch in a loop that lets an exception escape means a single
    malformed finding discards every well-formed finding beside it. In the first
    full benchmark run that cost three of four failed runs: the model returned
    one finding without a `file` and the whole review was thrown away.

    A missing `file` is repaired only when the request covered one known file.
    In a multi-file pass the finding cannot be attributed to anything, and
    guessing would put a real finding on the wrong file, so it is dropped.
    """
    comments: list[ReviewComment] = []
    dropped: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            dropped.append(f"not an object ({type(item).__name__})")
            continue
        candidate = dict(item)
        if not candidate.get("file") and default_file:
            candidate["file"] = default_file
        try:
            comments.append(ReviewComment.model_validate(candidate))
        except ValidationError as exc:
            first = exc.errors()[0]
            field = ".".join(str(part) for part in first.get("loc", ())) or "?"
            dropped.append(f"{field}: {first.get('msg', 'invalid')}")
    return comments, dropped


def parse_json_response(text: str):
    payload = extract_json_payload(text)
    return json.loads(payload)
