"""
Vision LLM plumbing for the two-pass thread extraction (see
app.services.extract_email.{triage_prompt,thread_prompt,thread_extract}).

Chat-completions only, against any OpenAI-compatible endpoint (OpenRouter,
OpenAI itself, or any other gateway) — no native Files/Responses upload.
PDF/DOCX/XLSX are always rendered to page images client-side first (see
thread_extract.collect_thread); the model never receives a raw file.

  - vision_provider / model_for / openai_api_key: provider + model routing.
  - text_block / image_block: content-block builders shared by both prompts.
  - chat_call: one system+content-blocks call with JSON-recovery retries.

Prompts are PII-scrubbed (core/pii.py) before every call.
"""
from __future__ import annotations

import asyncio
import base64
import json
import re

import httpx

from app.core.config import settings
from app.core.openai_url import openai_urls
from app.core.pii import scrub_text

# Fixed to match the prompt lab this pipeline was ported from — not tunable
# per-deployment the way batching/detail/DPI are.
_TEMPERATURE = 0.0
_MAX_TOKENS = 8000


def vision_provider() -> str:
    return (settings.llm_provider or "openai").strip().lower()


def model_for(provider: str = "", purpose: str = "vision") -> str:
    """The vision model to send. `provider`/`purpose` kept for call-site compatibility."""
    del provider, purpose
    return (settings.openai_vision_model or "gpt-4o").strip()


def openai_api_key() -> str:
    return (settings.openai_api_key or "").strip()


def text_block(text: str) -> dict:
    return {"type": "text", "text": text}


def image_block(jpeg_bytes: bytes, detail: str | None = None) -> dict:
    d = (detail or "low").strip().lower()
    if d not in ("low", "high"):
        d = "low"
    b64 = base64.b64encode(jpeg_bytes).decode("ascii")
    return {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": d}}


def _parse_json_loose(raw: str) -> dict | None:
    """Recover the outermost JSON object even if the model fenced it in
    markdown or added stray prefix/suffix text around it."""
    s = (raw or "").strip()
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    try:
        return json.loads(s)
    except Exception:
        pass
    start = s.find("{")
    if start == -1:
        return None
    depth, in_str, esc = 0, False, False
    for i, ch in enumerate(s[start:], start):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(s[start : i + 1])
                except Exception:
                    return None
    return None


async def chat_call(
    system_prompt: str,
    content_blocks: list[dict],
    model: str,
    api_key: str,
    *,
    label: str = "",
    retries: int = 3,
    force_json: bool = True,
    enable_thinking: bool = True,
) -> dict:
    """One system+content-blocks call, returning the parsed JSON reply.

    Retries recover from a stray non-JSON reply (asks again, force_json only
    on the first attempt — some models reject response_format on retry) and
    from transient HTTP failures, with a short backoff between attempts.
    `enable_thinking=False` turns off Qwen3-VL's <think> reasoning trace for
    calls that just need a fast, literal transcription (pass 2); pass 1 keeps
    it on since classification benefits from the extra reasoning.
    """
    api_root, _ = openai_urls(settings.openai_base_url)
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    last_err: object = None
    for attempt in range(retries):
        messages = []
        if (system_prompt or "").strip():
            messages.append({"role": "system", "content": scrub_text(system_prompt.strip())})
        messages.append({"role": "user", "content": content_blocks})
        payload: dict = {
            "model": model, "messages": messages,
            "temperature": _TEMPERATURE, "max_tokens": _MAX_TOKENS,
        }
        if force_json and attempt == 0:
            payload["response_format"] = {"type": "json_object"}
        if not enable_thinking:
            payload["chat_template_kwargs"] = {"enable_thinking": False}
        if vision_provider() == "openrouter":
            # OpenRouter hosts the same open model across several providers at
            # different prices (e.g. Qwen3-VL: DeepInfra vs Alibaba Cloud vs
            # NovitaAI can differ 30-50%). `sort: "price"` always routes to
            # whichever is cheapest right now for THIS exact model — same
            # weights, same quality, no accuracy tradeoff (unlike quantization
            # filtering, which is not enabled here on purpose for that reason).
            payload["provider"] = {"sort": "price"}
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(settings.openai_timeout)) as client:
                r = await client.post(f"{api_root}/v1/chat/completions", json=payload, headers=headers)
            if r.status_code != 200:
                last_err = f"{r.status_code}: {r.text[:500]}"
                await asyncio.sleep(2 + 3 * attempt)
                continue
            body = r.json()
            raw = ((body.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
            data = _parse_json_loose(raw)
            if data is None:
                last_err = "unparseable JSON reply"
                await asyncio.sleep(2 + 3 * attempt)
                continue
            return data
        except Exception as exc:
            last_err = exc
            await asyncio.sleep(2 + 3 * attempt)
    raise RuntimeError(f"{label or 'vision call'} failed after {retries} attempts: {last_err}")
