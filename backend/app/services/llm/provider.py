"""
LangChain provider factory — swap AI providers without touching call sites.

Because OpenAI, DeepSeek, vLLM (and most others) speak the OpenAI chat API,
LangChain's `ChatOpenAI` with a per-provider base_url/api_key/model covers them
all. The active provider + keys + model come from the runtime config overlay
(admin-editable), so switching from OpenAI to DeepSeek is a settings change, not
a code change.

  get_chat_model(db, kind="extraction"|"validation") -> a LangChain chat model
  chat(db, prompt, system=...) -> str            (provider-agnostic one-shot)
  test_provider(db, provider, prompt) -> dict     (used by the admin "Test" btn)

LangChain extras wired in: a ChatPromptTemplate + StrOutputParser chain (clean,
reusable prompt handling) and an in-memory LRU on model construction.
"""
from __future__ import annotations

import time
from functools import lru_cache

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.openai_url import openai_urls
from app.services.config.service import get_overlay

# Per-provider fallback metadata — openai model resolved from settings at runtime
PROVIDERS = {
    "openai":   {"base": "openai_base_url",   "key": "openai_api_key",   "model_setting": "extraction_model"},
    "deepseek": {"base": "deepseek_base_url", "key": "deepseek_api_key", "model": "deepseek-chat"},
}


@lru_cache(maxsize=8)
def _build_model(provider: str, model: str, base_url: str, api_key: str, temperature: float):
    """Construct (and cache) a LangChain chat model for the given provider."""
    from langchain_openai import ChatOpenAI

    _, langchain_base = openai_urls(base_url) if provider == "openai" else (base_url, base_url)

    return ChatOpenAI(
        model=model,
        api_key=api_key or "missing",
        base_url=langchain_base or None,
        temperature=temperature,
        timeout=60,
        max_retries=1,
    )


async def _resolve(db: AsyncSession, kind: str, provider_override: str | None = None):
    cfg = await get_overlay(db)
    provider = (provider_override or cfg.get("ai_provider") or "openai").lower()
    meta = PROVIDERS.get(provider, PROVIDERS["openai"])
    base_url = cfg.get(meta["base"]) or ""
    api_key = cfg.get(meta["key"]) or ""
    if kind == "validation":
        model = cfg.get("validation_model") or settings.validation_model
    elif kind == "agent":
        model = cfg.get("agent_chat_model") or settings.agent_chat_model
    else:
        # For openai provider fall back to settings.extraction_model; for others use their built-in default
        fallback = getattr(settings, meta.get("model_setting", ""), None) or meta.get("model", settings.extraction_model)
        model = cfg.get("extraction_model") or fallback
    return provider, model, base_url, api_key


async def get_chat_model(db: AsyncSession, kind: str = "extraction", provider: str | None = None):
    p, model, base_url, api_key = await _resolve(db, kind, provider)
    return _build_model(p, model, base_url, api_key, 0.0)


async def chat(db: AsyncSession, prompt: str, system: str | None = None, kind: str = "extraction") -> str:
    """Provider-agnostic one-shot completion using a LangChain prompt chain."""
    from langchain_core.output_parsers import StrOutputParser
    from langchain_core.prompts import ChatPromptTemplate

    model = await get_chat_model(db, kind)
    msgs = ([("system", system)] if system else []) + [("human", "{input}")]
    chain = ChatPromptTemplate.from_messages(msgs) | model | StrOutputParser()
    return await chain.ainvoke({"input": prompt})


async def active_config(db: AsyncSession, kind: str = "extraction") -> dict:
    """The resolved provider/model and whether an API key is configured.
    Lets features degrade gracefully (with a clear message) when no key is set."""
    p, model, _base_url, api_key = await _resolve(db, kind)
    key = (api_key or "").strip().lower()
    return {"provider": p, "model": model,
            "has_key": bool(key) and key not in ("change-me", "missing")}


async def test_provider(db: AsyncSession, provider: str | None, prompt: str) -> dict:
    """Build the model and do a tiny round-trip. Used by the admin Test button.
    Never raises — returns a structured result the UI can show."""
    from langchain_core.output_parsers import StrOutputParser
    from langchain_core.prompts import ChatPromptTemplate

    p, _, base_url, api_key = await _resolve(db, "validation", provider)
    _, model, _, _ = await _resolve(db, "validation", provider)
    if not api_key:
        return {"ok": False, "provider": p, "model": model, "error": "No API key configured for this provider."}
    t0 = time.time()
    try:
        llm = _build_model(p, model, base_url, api_key, 0.0)
        chain = (
            ChatPromptTemplate.from_messages([
                ("system", "You are a connectivity test. Be terse."),
                ("human", "{input}"),
            ])
            | llm
            | StrOutputParser()
        )
        reply = await chain.ainvoke({"input": prompt})
        return {"ok": True, "provider": p, "model": model,
                "latency_ms": int((time.time() - t0) * 1000), "reply": (reply or "").strip()[:200]}
    except Exception as e:
        err = str(e)[:300]
        if "404" in err and p == "openai":
            _, lb = openai_urls(base_url)
            err += f" (base_url={lb!r} — use https://api.openai.com or https://api.openai.com/v1)"
        return {"ok": False, "provider": p, "model": model, "error": err}
