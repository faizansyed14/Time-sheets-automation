"""
Ported from your project's `vision_client.py`.

Calls an OpenAI-compatible vision endpoint. For PDF/DOCX/XLSX it uploads the file
once and uses the Responses API (file_id); otherwise it sends base64 JPEG images.
Non-GPT models fall through to the vLLM chat path.
"""
from __future__ import annotations

import asyncio
import base64

import httpx

from app.core.config import settings
from app.core.openai_url import openai_urls
from app.core.pii import scrub_text


_AUX_TEXT_MAX = 24_000


VISION_PROVIDERS = ("openai", "vllm")          # deepseek has no vision model
VALIDATION_PROVIDERS = ("openai", "deepseek", "vllm")


def vision_provider() -> str:
    return (settings.vision_provider or "openai").strip().lower()


def validation_provider() -> str:
    return (settings.validation_provider or "openai").strip().lower()


def model_for(provider: str, purpose: str = "vision") -> str:
    """The model to send to THIS provider — never a global one.

    EXTRACTION_MODEL/VALIDATION_MODEL name the self-hosted (vLLM) models;
    OpenAI gets its own OPENAI_VISION_MODEL / OPENAI_VALIDATION_MODEL. This is
    what lets the admin flip a service's provider without touching models:
    sending a vLLM model name to OpenAI just 404s ("model does not exist")."""
    p = (provider or "openai").strip().lower()
    if p == "vllm":
        base = settings.vllm_model or (
            settings.extraction_model if purpose == "vision" else settings.validation_model)
        return (base or "").strip()
    if p == "deepseek":
        return "deepseek-chat"
    if purpose == "vision":
        return (settings.openai_vision_model or "gpt-4o").strip()
    return (settings.openai_validation_model or "gpt-4o-mini").strip()


def _vllm_verify():
    """httpx `verify` for the vLLM endpoint: a pinned CA bundle when configured
    (survives short-lived leaf-cert rotation), else standard verification.
    VLLM_TLS_VERIFY=false is a temporary test-phase escape hatch."""
    bundle = (settings.vllm_ca_bundle or "").strip()
    return bundle if bundle else settings.vllm_tls_verify


def _chat_endpoint(provider: str) -> tuple[str, str, str, object, int]:
    """Resolve an OpenAI-compatible provider to
    (chat_completions_url, auth_header, api_key, tls_verify, timeout).
    All three providers speak the same /v1/chat/completions API — only the
    base URL, key and (for vLLM) the TLS trust differ."""
    p = (provider or "openai").strip().lower()
    if p == "vllm":
        base = str(settings.vllm_base_url or "").rstrip("/")
        base = base if base.endswith("/v1") else base + "/v1"
        key = (settings.vllm_api_key or "").strip()
        auth = key if key.lower().startswith("bearer ") else f"Bearer {key}"
        return f"{base}/chat/completions", auth, key, _vllm_verify(), settings.vllm_timeout
    if p == "deepseek":
        base = str(settings.deepseek_base_url or "https://api.deepseek.com/v1").rstrip("/")
        base = base if base.endswith("/v1") else base + "/v1"
        key = (settings.deepseek_api_key or "").strip()
        return f"{base}/chat/completions", f"Bearer {key}", key, True, settings.openai_timeout
    api_root, _ = openai_urls(settings.openai_base_url)
    key = (settings.openai_api_key or "").strip()
    return f"{api_root}/v1/chat/completions", f"Bearer {key}", key, True, settings.openai_timeout


def _augment_prompt_with_text(prompt: str, aux_text: str | None) -> str:
    """Append the document's machine-extracted text as an AUTHORITATIVE source.

    For spreadsheets / .eml attachments the rendered image can be low quality
    (e.g. no LibreOffice to render an .xlsx), which is exactly when a vision
    model tends to hallucinate placeholder values. The exact cell text removes
    that ambiguity, so we hand it to the model and tell it to trust it."""
    txt = (aux_text or "").strip()
    if not txt:
        return prompt
    if len(txt) > _AUX_TEXT_MAX:
        txt = txt[:_AUX_TEXT_MAX] + "\n[... truncated ...]"
    return (
        prompt
        + "\n\n═══════════════════════════════════════\n"
        + "AUTHORITATIVE DOCUMENT TEXT (extracted directly from the file)\n"
        + "═══════════════════════════════════════\n"
        + "Trust this text over the image for the employee name, employee ID, "
        + "month, year and every date. Use the image only for layout/column "
        + "headers. If a value is not present here or in the image, leave it "
        + "EMPTY — never output an example/placeholder.\n---\n"
        + txt
        + "\n---"
    )


async def extract_timesheet(
    images_jpeg: list[bytes],
    prompt: str,
    system_prompt: str | None,
    model: str,
    image_detail: str | None,
    file_bytes: bytes | None,
    file_type: str | None,
    filename: str | None,
    aux_text: str | None = None,
) -> dict:
    prompt = _augment_prompt_with_text(prompt, aux_text)
    m = (model or "").strip()
    provider = vision_provider()
    if provider == "openai":
        return await _extract_openai(images_jpeg, prompt, system_prompt, m, image_detail,
                                     file_bytes, file_type, filename)
    return await _chat_compatible(provider, images_jpeg, prompt, system_prompt, m, image_detail)


async def _extract_openai(images_jpeg, prompt, system_prompt, model, image_detail,
                          file_bytes, file_type, filename) -> dict:
    api_key = (settings.openai_api_key or "").strip()
    if not api_key or api_key.lower() == "change-me":
        raise RuntimeError("OPENAI_API_KEY is not set. Add it in .env.")
    if file_type in {"pdf", "docx", "xlsx"} and file_bytes:
        try:
            return await _openai_by_file_id(file_bytes, filename or f"timesheet.{file_type}",
                                            file_type, prompt, system_prompt, model, api_key)
        except Exception:
            pass  # fall back to images
    return await _openai_by_images(images_jpeg, prompt, system_prompt, model, image_detail, api_key)


async def _openai_by_images(images_jpeg, prompt, system_prompt, model, image_detail, api_key) -> dict:
    prompt = scrub_text(prompt)  # last line of defence: no PII leaves in prompt text
    api_root, _ = openai_urls(settings.openai_base_url)
    detail = (image_detail or "").strip().lower()
    if detail not in {"low", "high"}:
        detail = "low"
    content: list[dict] = []
    for img in images_jpeg:
        b64 = base64.b64encode(img).decode("utf-8")
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": detail}})
    content.append({"type": "text", "text": prompt})
    messages = []
    if (system_prompt or "").strip():
        messages.append({"role": "system", "content": system_prompt.strip()})
    messages.append({"role": "user", "content": content})
    payload: dict = {"model": model, "messages": messages}
    if model.startswith("gpt-5"):
        payload["max_completion_tokens"] = 8192
        payload["reasoning_effort"] = "high"
    else:
        payload["max_tokens"] = 4096
        payload["temperature"] = 0.0
        # Guarantee parseable JSON on models that support it (gpt-4o / -mini /
        # 4.1) — no stray markdown fence or prose can break the parser. The
        # prompt already instructs the model to return JSON.
        if getattr(settings, "vision_json_mode", True) and model.lower().startswith(("gpt-4o", "gpt-4.1")):
            payload["response_format"] = {"type": "json_object"}
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=httpx.Timeout(settings.openai_timeout)) as client:
        r = await client.post(f"{api_root}/v1/chat/completions", json=payload, headers=headers)
        if r.status_code != 200:
            raise RuntimeError(f"OpenAI returned {r.status_code}: {r.text[:500]}")
        return r.json()


async def _openai_by_file_id(file_bytes, filename, file_type, prompt, system_prompt, model, api_key) -> dict:
    prompt = scrub_text(prompt)
    api_root, _ = openai_urls(settings.openai_base_url)
    mime = {"pdf": "application/pdf",
            "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}.get(file_type, "application/octet-stream")
    async with httpx.AsyncClient(timeout=httpx.Timeout(settings.openai_timeout)) as client:
        fr = await client.post(f"{api_root}/v1/files",
                               headers={"Authorization": f"Bearer {api_key}"},
                               data={"purpose": "user_data"},
                               files={"file": (filename, file_bytes, mime)})
        if fr.status_code != 200:
            raise RuntimeError(f"files.create {fr.status_code}: {fr.text[:300]}")
        file_id = (fr.json() or {}).get("id")
        if not file_id:
            raise RuntimeError("no file id")
        try:
            payload: dict = {"model": model,
                             "input": [{"role": "user", "content": [
                                 {"type": "input_file", "file_id": file_id},
                                 {"type": "input_text", "text": prompt}]}]}
            if (system_prompt or "").strip():
                payload["instructions"] = system_prompt.strip()
            if model.startswith("gpt-5"):
                payload["max_output_tokens"] = 8192
                payload["reasoning"] = {"effort": "high"}
            else:
                payload["max_output_tokens"] = 4096
            rr = await client.post(f"{api_root}/v1/responses", json=payload,
                                   headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"})
            if rr.status_code != 200:
                raise RuntimeError(f"responses {rr.status_code}: {rr.text[:300]}")
            return rr.json()
        finally:
            # The upload is transient model input holding employee PII — it must
            # not persist in OpenAI's file storage after the model has answered.
            try:
                await client.delete(f"{api_root}/v1/files/{file_id}",
                                    headers={"Authorization": f"Bearer {api_key}"})
            except Exception:
                pass


async def _chat_compatible(provider, images_jpeg, prompt, system_prompt, model, image_detail) -> dict:
    """Generic OpenAI-compatible /v1/chat/completions call for any non-native
    provider (vLLM, DeepSeek). Sends base64 images + text; no file upload and
    no json_mode (those are OpenAI-only features)."""
    prompt = scrub_text(prompt)
    url, auth, api_key, verify, timeout = _chat_endpoint(provider)
    if not api_key:
        raise RuntimeError(f"{provider} API key is not set. Add it in AI Settings.")
    detail = (image_detail or "low").strip().lower()
    content: list[dict] = []
    for img in images_jpeg:
        b64 = base64.b64encode(img).decode("utf-8")
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": detail}})
    content.append({"type": "text", "text": prompt})
    messages = []
    if (system_prompt or "").strip():
        messages.append({"role": "system", "content": system_prompt.strip()})
    messages.append({"role": "user", "content": content})
    payload = {"model": model, "max_tokens": min(settings.vllm_max_tokens, 4096),
               "temperature": settings.vllm_temperature, "messages": messages}
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout), verify=verify) as client:
        # Self-hosted boxes over VPN/wifi drop the odd TCP connect — one quick
        # retry saves the whole sheet from falling into the slow per-file
        # fallback (which would call the same flaky endpoint again anyway).
        for attempt in (1, 2):
            try:
                r = await client.post(url, json=payload, headers={"Authorization": auth})
                break
            except httpx.ConnectError:
                if attempt == 2:
                    raise
                await asyncio.sleep(2)
        if r.status_code != 200:
            raise RuntimeError(f"{provider} returned {r.status_code}: {r.text[:500]}")
        return r.json()


async def _extract_vllm(images_jpeg, prompt, system_prompt, model, image_detail) -> dict:
    """Back-compat wrapper — vLLM is one OpenAI-compatible provider."""
    return await _chat_compatible("vllm", images_jpeg, prompt, system_prompt, model, image_detail)


async def validate_extraction(prompt: str, system_prompt: str | None, model: str = "gpt-4o-mini") -> dict:
    """Text-only call routed to the configured VALIDATION provider."""
    provider = validation_provider()
    if provider != "openai":
        # DeepSeek / vLLM — a plain text chat call (scrubbed inside).
        return await _chat_compatible(provider, [], prompt, system_prompt, model, "low")
    prompt = scrub_text(prompt)
    url, auth, api_key, verify, timeout = _chat_endpoint("openai")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY required for text validation.")
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})
    payload: dict = {"model": model, "messages": messages, "temperature": 0.0, "max_tokens": 8192}
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as client:
        r = await client.post(url, json=payload,
                              headers={"Authorization": auth, "Content-Type": "application/json"})
        if r.status_code != 200:
            raise RuntimeError(f"validation {r.status_code}: {r.text[:300]}")
        return r.json()
