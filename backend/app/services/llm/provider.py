"""
LangChain OpenAI-compatible chat model factory.

ChatOpenAI works against any OpenAI-compatible chat-completions endpoint, not
only OpenAI itself — provider, keys, base URL and model names all come from
.env (`app.core.config.settings`). settings.llm_provider is a display label
only here (e.g. "openrouter"); ChatOpenAI just POSTs to whatever
openai_base_url is configured.
"""
from __future__ import annotations

import time
from functools import lru_cache

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.openai_url import openai_urls


@lru_cache(maxsize=4)
def _build_model(model: str, base_url: str, api_key: str, temperature: float):
    from langchain_openai import ChatOpenAI

    _, langchain_base = openai_urls(base_url)
    return ChatOpenAI(
        model=model,
        api_key=api_key or "missing",
        base_url=langchain_base or None,
        temperature=temperature,
        timeout=60,
        max_retries=1,
    )


def _resolve(kind: str) -> tuple[str, str, str, str]:
    model = (
        settings.agent_chat_model or "gpt-4o-mini"
        if kind == "agent"
        else settings.extraction_model or settings.openai_vision_model or "gpt-4o"
    )
    provider = (settings.llm_provider or "openai").strip().lower()
    return provider, model, settings.openai_base_url or "", settings.openai_api_key or ""


async def get_chat_model(db: AsyncSession, kind: str = "extraction", provider: str | None = None):
    del db, provider
    _p, model, base_url, api_key = _resolve(kind)
    return _build_model(model, base_url, api_key, 0.0)


async def active_config(db: AsyncSession, kind: str = "extraction") -> dict:
    del db
    provider, model, _base_url, api_key = _resolve(kind)
    key = (api_key or "").strip().lower()
    return {
        "provider": provider,
        "model": model,
        "has_key": bool(key) and key not in ("change-me", "missing"),
    }


async def test_provider(db: AsyncSession, provider: str | None, prompt: str) -> dict:
    del provider
    from langchain_core.output_parsers import StrOutputParser
    from langchain_core.prompts import ChatPromptTemplate

    resolved_provider, model, base_url, api_key = _resolve("extraction")
    if not api_key:
        return {"ok": False, "provider": resolved_provider, "model": model,
                "error": "No OPENAI_API_KEY in .env."}
    t0 = time.time()
    try:
        llm = _build_model(model, base_url, api_key, 0.0)
        chain = (
            ChatPromptTemplate.from_messages([
                ("system", "You are a connectivity test. Be terse."),
                ("human", "{input}"),
            ])
            | llm
            | StrOutputParser()
        )
        reply = await chain.ainvoke({"input": prompt})
        return {"ok": True, "provider": resolved_provider, "model": model,
                "latency_ms": int((time.time() - t0) * 1000), "reply": (reply or "").strip()[:200]}
    except Exception as e:
        err = str(e)[:300]
        if "404" in err:
            _, lb = openai_urls(base_url)
            err += f" (base_url={lb!r} — use https://api.openai.com or https://api.openai.com/v1, " \
                   f"or https://openrouter.ai/api/v1 for OpenRouter)"
        return {"ok": False, "provider": resolved_provider, "model": model, "error": err}
