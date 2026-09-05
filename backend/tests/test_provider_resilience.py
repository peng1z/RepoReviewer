import asyncio
import time

import litellm
import pytest

from repo_reviewer import provider
from repo_reviewer import provider as provider_module
from repo_reviewer.models import ReviewComment
from repo_reviewer.provider import drop_unreachable_lines, resolve_model


@pytest.fixture(autouse=True)
def _reset_policy():
    yield
    provider._limiter = None
    provider._max_retries = None
    provider._retry_base = None
    provider._timeout = None


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


def test_retry_after_is_read_from_headers() -> None:
    class Resp:
        headers = {"retry-after": "7"}

    exc = RuntimeError("nope")
    exc.response = Resp()  # type: ignore[attr-defined]
    assert provider.retry_after_seconds(exc) == 7.0


def test_retry_after_is_read_from_the_openrouter_payload() -> None:
    exc = RuntimeError(
        '{"error":{"code":429,"metadata":{"provider_error_code":"upstream_429",'
        '"retry_after_seconds":5,"headers":{"Retry-After":"5"}}}}'
    )
    assert provider.retry_after_seconds(exc) == 5.0


def test_retry_after_absent_returns_none() -> None:
    assert provider.retry_after_seconds(RuntimeError("plain failure")) is None


@pytest.mark.asyncio
async def test_provider_hint_overrides_a_shorter_backoff(monkeypatch) -> None:
    provider.configure_llm(requests_per_minute=0, max_retries=1, retry_base_seconds=0.0)
    slept: list[float] = []

    async def fake_sleep(d):
        slept.append(d)

    async def limited_then_ok(**kwargs):
        if not slept:
            raise litellm.RateLimitError(
                '{"metadata":{"retry_after_seconds":4}}', llm_provider="openrouter", model="m"
            )

        class R:
            choices = [type("C", (), {"message": type("M", (), {"content": "[]"})()})()]

        return R()

    monkeypatch.setattr(provider.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(provider, "acompletion", limited_then_ok)
    await provider.structured_completion(
        provider="openrouter", model="openrouter/x", system_prompt="s", user_prompt="u"
    )
    assert slept and slept[0] >= 4.0, f"should honour the 4s hint, slept {slept}"


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ([{"file": "a.py", "issue": "x"}], 1),
        ({"comments": [{"file": "a.py", "issue": "x"}, {"file": "b.py", "issue": "y"}]}, 2),
        ({"findings": [{"file": "a.py", "issue": "x"}]}, 1),
        ({"file": "a.py", "line": 3, "issue": "x", "suggestion": "y"}, 1),
        ([], 0),
        ({}, 0),
        ("nonsense", 0),
        ([{"file": "a.py", "issue": "x"}, "stray string"], 1),
    ],
)
def test_coerce_comment_payload_handles_model_shapes(payload, expected) -> None:
    assert len(provider.coerce_comment_payload(payload)) == expected


def test_wrapped_payload_is_not_iterated_as_keys() -> None:
    """Iterating {"comments": [...]} yields the string "comments", not findings."""
    out = provider.coerce_comment_payload({"comments": [{"file": "a.py", "issue": "x"}]})
    assert out == [{"file": "a.py", "issue": "x"}]


@pytest.mark.asyncio
async def test_a_request_timeout_is_passed_to_the_provider(monkeypatch) -> None:
    """A hung call must be abandoned; without this a pilot stalled for 18 minutes."""
    provider.configure_llm(requests_per_minute=0, max_retries=0, request_timeout_seconds=42.0)
    seen: dict = {}

    async def capture(**kwargs):
        seen.update(kwargs)

        class R:
            choices = [type("C", (), {"message": type("M", (), {"content": "[]"})()})()]

        return R()

    monkeypatch.setattr(provider, "acompletion", capture)
    await provider.structured_completion(
        provider="openrouter", model="openrouter/x", system_prompt="s", user_prompt="u"
    )
    assert seen["timeout"] == 42.0


@pytest.mark.asyncio
async def test_a_timeout_is_retried(monkeypatch) -> None:
    provider.configure_llm(requests_per_minute=0, max_retries=2, retry_base_seconds=0.0)
    attempts = {"n": 0}

    async def slow_then_ok(**kwargs):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise litellm.Timeout("timed out", model="m", llm_provider="openrouter")

        class R:
            choices = [type("C", (), {"message": type("M", (), {"content": "[]"})()})()]

        return R()

    monkeypatch.setattr(provider, "acompletion", slow_then_ok)
    assert await provider.structured_completion(
        provider="openrouter", model="openrouter/x", system_prompt="s", user_prompt="u"
    ) == "[]"
    assert attempts["n"] == 2


# --- malformed findings must not discard the whole review -----------------

# The shape the model actually returned in the first full benchmark run: every
# field present except `file`. It raised ValidationError and took the entire
# review with it, three times out of four failed runs.
MISSING_FILE = {
    "line": 105,
    "severity": "medium",
    "issue": "Magic number should be extracted to a named constant.",
    "suggestion": "Introduce a module-level constant.",
}
WELL_FORMED = {
    "file": "pkg/core.py",
    "line": 12,
    "severity": "high",
    "issue": "Off-by-one in the loop bound.",
    "suggestion": "Use <= instead of <.",
}


def test_one_bad_finding_no_longer_discards_the_good_ones() -> None:
    comments, dropped = provider.build_comments([WELL_FORMED, MISSING_FILE, WELL_FORMED])
    assert len(comments) == 2
    assert len(dropped) == 1
    assert "file" in dropped[0]


def test_missing_file_is_repaired_when_one_file_was_reviewed() -> None:
    """review_agent sends a single file, so the finding can only belong to it."""
    comments, dropped = provider.build_comments([MISSING_FILE], default_file="pkg/core.py")
    assert dropped == []
    assert comments[0].file == "pkg/core.py"
    assert comments[0].line == 105


def test_missing_file_is_dropped_when_many_files_were_reviewed() -> None:
    """Guessing would attach a real finding to the wrong file."""
    comments, dropped = provider.build_comments([MISSING_FILE])
    assert comments == []
    assert len(dropped) == 1


def test_a_present_file_is_never_overwritten_by_the_default() -> None:
    comments, _ = provider.build_comments([WELL_FORMED], default_file="other.py")
    assert comments[0].file == "pkg/core.py"


@pytest.mark.parametrize(
    "item",
    [
        "a bare string",
        42,
        None,
        {"file": "a.py", "severity": "medium", "suggestion": "x"},          # no issue
        {"file": "a.py", "severity": "catastrophic", "issue": "x", "suggestion": "y"},
    ],
)
def test_entries_that_cannot_be_repaired_are_dropped(item) -> None:
    comments, dropped = provider.build_comments([item], default_file="a.py")
    assert comments == []
    assert len(dropped) == 1


def test_dropped_reasons_name_the_offending_field() -> None:
    _, dropped = provider.build_comments([{"file": "a.py", "issue": "x", "suggestion": "y"}])
    assert "severity" in dropped[0]


def test_empty_input_is_not_an_error() -> None:
    assert provider.build_comments([]) == ([], [])


# --- API key hints --------------------------------------------------------


@pytest.mark.parametrize(
    ("provider", "expected"),
    [("openai", "OPENAI_API_KEY"), ("anthropic", "ANTHROPIC_API_KEY"),
     ("openrouter", "OPENROUTER_API_KEY"), ("groq", "GROQ_API_KEY"),
     ("ollama", "API_KEY")],
)
def test_each_provider_maps_to_its_environment_variable(provider, expected) -> None:
    assert provider_module.env_var_for_provider(provider) == expected


def test_a_provider_whose_key_is_absent_is_reported(monkeypatch) -> None:
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    assert provider_module.missing_api_key("groq") == "GROQ_API_KEY"


def test_a_provider_whose_key_is_present_is_not_reported(monkeypatch) -> None:
    """The old version returned the name either way, so the CLI nagged
    everyone regardless of whether they had already set the key."""
    monkeypatch.setenv("GROQ_API_KEY", "sk-set")
    assert provider_module.missing_api_key("groq") is None


@pytest.mark.parametrize(
    ("provider", "model", "expected"),
    [
        # The case that motivated the fix: every OpenRouter model id is
        # `vendor/model`, so the old "leave it alone if it has a slash" rule
        # handed litellm `minimax/...`, which routes to MiniMax's own API.
        ("openrouter", "minimax/minimax-m2.7:free", "openrouter/minimax/minimax-m2.7:free"),
        ("openrouter", "openai/gpt-4.1-mini", "openrouter/openai/gpt-4.1-mini"),
        # Already qualified: left alone rather than prefixed twice.
        ("openrouter", "openrouter/minimax/minimax-m2.7:free", "openrouter/minimax/minimax-m2.7:free"),
        ("openai", "openai/gpt-4.1-mini", "openai/gpt-4.1-mini"),
        # Bare names still pick up their provider.
        ("openai", "gpt-4.1-mini", "openai/gpt-4.1-mini"),
        ("anthropic", "claude-3-5-haiku-latest", "anthropic/claude-3-5-haiku-latest"),
        ("groq", "llama-3.3-70b-versatile", "groq/llama-3.3-70b-versatile"),
        ("ollama", "llama3", "ollama/llama3"),
    ],
)
def test_resolve_model_qualifies_with_the_requested_provider(provider, model, expected) -> None:
    assert resolve_model(provider, model) == expected


def test_resolve_model_falls_back_to_the_provider_alias() -> None:
    assert resolve_model("openrouter", None) == "openrouter/openai/gpt-4.1-mini"
    assert resolve_model("openai", None) == "openai/gpt-4.1-mini"
    assert resolve_model("unknown-provider", None) == "openai/gpt-4.1-mini"


def _comment(file: str, line: int | None) -> ReviewComment:
    return ReviewComment(file=file, line=line, severity="low", issue="i", suggestion="s")


def test_drop_unreachable_lines_clears_positions_past_the_end_of_the_file() -> None:
    # A review of psf/requests put three findings on lines 62, 66 and 69 of a
    # 51-line file. extract_snippet returned "" for all three, which was the
    # signal, and it was discarded.
    comments = [_comment("a.py", 62), _comment("a.py", 20), _comment("a.py", 51)]

    notes = drop_unreachable_lines(comments, {"a.py": 51})

    assert [item.line for item in comments] == [None, 20, 51]
    assert len(notes) == 1
    assert "a.py:62 is outside the file (51 lines)" in notes[0]


def test_drop_unreachable_lines_keeps_the_finding_itself() -> None:
    # One of the three real cases was a valid point about `assert` being
    # stripped under `python -O`. The observation survives; only the position
    # it cited is dropped.
    comment = _comment("a.py", 999)
    comment.issue = "assert can be disabled with -O"

    drop_unreachable_lines([comment], {"a.py": 10})

    assert comment.issue == "assert can be disabled with -O"
    assert comment.line is None


def test_drop_unreachable_lines_rejects_a_line_below_one() -> None:
    comments = [_comment("a.py", 0), _comment("a.py", -3)]

    notes = drop_unreachable_lines(comments, {"a.py": 10})

    assert [item.line for item in comments] == [None, None]
    assert len(notes) == 2


def test_drop_unreachable_lines_leaves_unknown_files_and_absent_lines_alone() -> None:
    unknown = _comment("other.py", 4000)
    absent = _comment("a.py", None)

    notes = drop_unreachable_lines([unknown, absent], {"a.py": 10})

    assert unknown.line == 4000
    assert absent.line is None
    assert notes == []
