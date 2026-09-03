import asyncio
import time

import litellm
import pytest

from repo_reviewer import provider


@pytest.fixture(autouse=True)
def _reset_policy():
    yield
    provider._limiter = None
    provider._max_retries = None
    provider._retry_base = None


@pytest.mark.asyncio
async def test_rate_limiter_paces_calls_beyond_its_budget() -> None:
    limiter = provider.RateLimiter(max_calls=2, period=0.3)
    started = time.monotonic()
    for _ in range(4):
        await limiter.acquire()
    # 4 calls at 2 per 0.3s window cannot finish inside one window.
    assert time.monotonic() - started >= 0.3


@pytest.mark.asyncio
async def test_rate_limiter_is_disabled_when_budget_is_zero() -> None:
    limiter = provider.RateLimiter(max_calls=0, period=60)
    started = time.monotonic()
    for _ in range(50):
        await limiter.acquire()
    assert time.monotonic() - started < 0.5


@pytest.mark.asyncio
async def test_retries_a_rate_limit_error_then_succeeds(monkeypatch) -> None:
    provider.configure_llm(requests_per_minute=0, max_retries=3, retry_base_seconds=0.0)
    attempts = {"n": 0}

    async def flaky(**kwargs):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise litellm.RateLimitError("429", llm_provider="openrouter", model="m")

        class R:
            choices = [type("C", (), {"message": type("M", (), {"content": "[]"})()})()]

        return R()

    monkeypatch.setattr(provider, "acompletion", flaky)
    result = await provider.structured_completion(
        provider="openrouter", model="openrouter/x", system_prompt="s", user_prompt="u"
    )
    assert result == "[]"
    assert attempts["n"] == 3


@pytest.mark.asyncio
async def test_gives_up_after_max_retries(monkeypatch) -> None:
    provider.configure_llm(requests_per_minute=0, max_retries=2, retry_base_seconds=0.0)
    attempts = {"n": 0}

    async def always_limited(**kwargs):
        attempts["n"] += 1
        raise litellm.RateLimitError("429", llm_provider="openrouter", model="m")

    monkeypatch.setattr(provider, "acompletion", always_limited)
    with pytest.raises(litellm.RateLimitError):
        await provider.structured_completion(
            provider="openrouter", model="openrouter/x", system_prompt="s", user_prompt="u"
        )
    assert attempts["n"] == 3  # initial try plus two retries


@pytest.mark.asyncio
async def test_permanent_errors_are_not_retried(monkeypatch) -> None:
    """Retrying a bad request would burn quota for nothing."""
    provider.configure_llm(requests_per_minute=0, max_retries=5, retry_base_seconds=0.0)
    attempts = {"n": 0}

    async def bad_request(**kwargs):
        attempts["n"] += 1
        raise litellm.BadRequestError("nope", llm_provider="openrouter", model="m")

    monkeypatch.setattr(provider, "acompletion", bad_request)
    with pytest.raises(litellm.BadRequestError):
        await provider.structured_completion(
            provider="openrouter", model="openrouter/x", system_prompt="s", user_prompt="u"
        )
    assert attempts["n"] == 1
