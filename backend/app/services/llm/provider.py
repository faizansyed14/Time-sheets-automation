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

from app.services.config.service import get_overlay

# Per-provider defaults: (base_url_key, api_key_key, default_model)
PROVIDERS = {
    "openai": {"base": "openai_base_url", "key": "openai_api_key", "model": "gpt-4o"},
    "deepseek": {"base": "deepseek_base_url", "key": "deepseek_api_key", "model": "deepseek-chat"},
}


@lru_cache(maxsize=8)
def _build_model(provider: str, model: str, base_url: str, api_key: str, temperature: float):
    """Construct (and cache) a LangChain chat model for the given provider."""
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=model,
        api_key=api_key or "missing",
        base_url=base_url or None,
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
        model = cfg.get("validation_model") or "gpt-4o-mini"
    else:
        model = cfg.get("extraction_model") or meta["model"]
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


async def test_provider(db: AsyncSession, provider: str | None, prompt: str) -> dict:
    """Build the model and do a tiny round-trip. Used by the admin Test button.
    Never raises — returns a structured result the UI can show."""
    p, model, base_url, api_key = await _resolve(db, "extraction", provider)
    if not api_key:
        return {"ok": False, "provider": p, "model": model, "error": "No API key configured for this provider."}
    t0 = time.time()
    try:
        reply = await chat(db, prompt, system="You are a connectivity test. Be terse.")
        return {"ok": True, "provider": p, "model": model,
                "latency_ms": int((time.time() - t0) * 1000), "reply": (reply or "").strip()[:200]}
    except Exception as e:
        return {"ok": False, "provider": p, "model": model, "error": str(e)[:300]}
