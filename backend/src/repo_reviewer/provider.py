from __future__ import annotations

import json
import os
import re

from litellm import acompletion

from .models import ReviewComment


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
    response = await acompletion(
        model=resolved_model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.2,
    )
    return response.choices[0].message.content or ""


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
