"""Normalize OpenAI-compatible base URLs for different HTTP clients."""
from __future__ import annotations


def openai_urls(base_url: str | None) -> tuple[str, str]:
    """Return (api_root, langchain_base).

    api_root:     https://api.openai.com          — append /v1/chat/completions (httpx)
    langchain_base: https://api.openai.com/v1   — pass to ChatOpenAI.base_url

    Accepts either form in .env / admin settings.
    """
    raw = (base_url or "https://api.openai.com").strip().rstrip("/")
    if raw.endswith("/v1"):
        return raw[:-3], raw
    return raw, f"{raw}/v1"
