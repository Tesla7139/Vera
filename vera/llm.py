"""Thin async LLM wrapper returning schema-constrained JSON.

Providers: Claude (Claude API or Microsoft Foundry) via the Anthropic SDK, or GPT / DeepSeek
deployments on Azure via the OpenAI SDK. Selected in config.PROVIDER.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from urllib.parse import urlparse

from . import config

log = logging.getLogger("vera.llm")

_clients: dict[str, object] = {}
_sem = asyncio.Semaphore(config.LLM_CONCURRENCY)
_cache: dict[str, dict] = {}
# Server-side refusal fallbacks exist on the Claude API only (not on Foundry); disabled automatically
# if rejected. A refused message falls back to templates.
_use_fallbacks = config.PROVIDER == "anthropic"
# Deployments that rejected reasoning_effort (e.g. non-reasoning models); learned at runtime.
_no_effort: set[str] = set()


def _anthropic_client():
    import anthropic
    if "anthropic" not in _clients:
        if config.PROVIDER == "foundry":
            # Reads ANTHROPIC_FOUNDRY_API_KEY and ANTHROPIC_FOUNDRY_RESOURCE / ANTHROPIC_FOUNDRY_BASE_URL.
            _clients["anthropic"] = anthropic.AsyncAnthropicFoundry(timeout=config.LLM_TIMEOUT_S, max_retries=1)
        else:
            _clients["anthropic"] = anthropic.AsyncAnthropic(timeout=config.LLM_TIMEOUT_S, max_retries=1)
    return _clients["anthropic"]


def _azure_client():
    from openai import AsyncAzureOpenAI
    if "azure" not in _clients:
        u = urlparse(config.AZURE_ENDPOINT)
        _clients["azure"] = AsyncAzureOpenAI(azure_endpoint=f"{u.scheme}://{u.hostname}", api_key=config.AZURE_API_KEY,
                                             api_version=config.AZURE_API_VERSION,
                                             timeout=config.LLM_TIMEOUT_S, max_retries=1)
    return _clients["azure"]


def _get_client():
    return _azure_client() if config.PROVIDER == "azure_openai" else _anthropic_client()


async def _claude(system: str, user: str, schema: dict, max_tokens: int, model: str) -> dict | None:
    import anthropic
    global _use_fallbacks
    params = dict(
        model=model,
        max_tokens=max_tokens,
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user}],
        output_config={"effort": config.EFFORT, "format": {"type": "json_schema", "schema": schema}},
    )
    client = _anthropic_client()
    try:
        if _use_fallbacks:
            try:
                resp = await client.beta.messages.create(
                    **params, betas=["server-side-fallback-2026-07-01"], fallbacks="default")
            except (TypeError, anthropic.BadRequestError) as e:
                log.warning("refusal fallbacks unavailable (%s); continuing without", e)
                _use_fallbacks = False
                resp = await client.messages.create(**params)
        else:
            resp = await client.messages.create(**params)
    except anthropic.APIConnectionError as e:
        log.warning("LLM connection error: %s", e)
        return None
    except anthropic.RateLimitError as e:
        log.warning("LLM rate limited: %s", e)
        return None
    except anthropic.APIStatusError as e:
        log.warning("LLM API error %s: %s", e.status_code, e.message)
        return None
    if resp.stop_reason == "refusal":
        return None
    text = next((b.text for b in resp.content if b.type == "text"), None)
    return _parse(text)


async def _azure(system: str, user: str, schema: dict, max_tokens: int, model: str) -> dict | None:
    import openai
    client = _azure_client()
    params = dict(
        model=model,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        response_format={"type": "json_schema", "json_schema": {"name": "vera_output", "schema": schema, "strict": True}},
        max_completion_tokens=max(max_tokens, 4000),  # reasoning tokens count against this budget
        seed=7,
    )
    if model not in _no_effort:
        params["reasoning_effort"] = config.EFFORT
    try:
        try:
            resp = await client.chat.completions.create(**params)
        except openai.BadRequestError as e:
            msg = str(e.message).lower()
            if "reasoning_effort" in msg or "seed" in msg:
                _no_effort.add(model)
                params.pop("reasoning_effort", None)
                params.pop("seed", None)
                resp = await client.chat.completions.create(**params)
            else:
                raise
    except openai.APIConnectionError as e:
        log.warning("LLM connection error: %s", e)
        return None
    except openai.RateLimitError as e:
        log.warning("LLM rate limited: %s", e)
        return None
    except openai.APIStatusError as e:
        log.warning("LLM API error %s: %s", e.status_code, e.message)
        return None
    choice = resp.choices[0]
    if getattr(choice.message, "refusal", None):
        return None
    return _parse(choice.message.content)


def _parse(text: str | None) -> dict | None:
    if not text:
        return None
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


async def complete_json(system: str, user: str, schema: dict, max_tokens: int = 2000,
                        model: str | None = None) -> dict | None:
    """Returns parsed JSON or None on any failure. Identical inputs return the cached result,
    which keeps the bot deterministic for repeated calls."""
    if not config.LLM_ENABLED:
        return None
    model = model or config.MODEL
    key = hashlib.sha256(json.dumps([config.PROVIDER, model, system, user, schema], sort_keys=True).encode()).hexdigest()
    if key in _cache:
        return _cache[key]
    async with _sem:
        if config.PROVIDER == "azure_openai":
            data = await _azure(system, user, schema, max_tokens, model)
        else:
            data = await _claude(system, user, schema, max_tokens, model)
    if data is not None:
        _cache[key] = data
    return data
