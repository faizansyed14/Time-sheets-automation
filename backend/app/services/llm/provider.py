"""
LangChain OpenAI-compatible chat model factory.

ChatOpenAI works against any OpenAI-compatible chat-completions endpoint, not
only OpenAI itself — provider, keys, base URL and model names all come from
.env (`app.core.config.settings`). settings.llm_provider is a display label
only here (e.g. "openrouter"); ChatOpenAI just POSTs to whatever
openai_base_url is configured.
"""
from __future__ import annotations

from functools import lru_cache

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.openai_url import openai_urls


@lru_cache(maxsize=4)
def _build_model(model: str, base_url: str, api_key: str, temperature: float):
    from langchain_openai import ChatOpenAI

    _, langchain_base = openai_urls(base_url)
    extra_body = {}
    if (settings.llm_provider or "").strip().lower() == "openrouter":
        # Same reasoning as vision_client.chat_call: route to whichever
        # provider is currently cheapest for this exact model, no quality
        # tradeoff (see that module for the quantization caveat).
        extra_body["provider"] = {"sort": "price"}
    return ChatOpenAI(
        model=model,
        api_key=api_key or "missing",
        base_url=langchain_base or None,
        temperature=temperature,
        timeout=60,
        max_retries=1,
        extra_body=extra_body or None,
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
