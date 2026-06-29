"""
Ported from your project's `vision_client.py`.

Calls an OpenAI-compatible vision endpoint. For PDF/DOCX/XLSX it uploads the file
once and uses the Responses API (file_id); otherwise it sends base64 JPEG images.
Non-GPT models fall through to the vLLM chat path.
"""
from __future__ import annotations

import base64

import httpx

from app.core.config import settings
from app.core.openai_url import openai_urls


_AUX_TEXT_MAX = 24_000


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
    is_gpt = m.startswith(("gpt-4", "gpt-5")) or m.lower() in {"gpt-4o", "gpt-4o-mini", "gpt-4.1", "gpt-5.4"}
    if is_gpt:
        return await _extract_openai(images_jpeg, prompt, system_prompt, m, image_detail,
                                     file_bytes, file_type, filename)
    return await _extract_vllm(images_jpeg, prompt, system_prompt, m, image_detail)


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


async def _extract_vllm(images_jpeg, prompt, system_prompt, model, image_detail) -> dict:
    base_url = str(settings.vllm_base_url).rstrip("/")
    api_key = (settings.vllm_api_key or "").strip()
    if not api_key:
        raise RuntimeError("VLLM_API_KEY is not set.")
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
    auth = api_key if api_key.lower().startswith("bearer ") else f"Bearer {api_key}"
    async with httpx.AsyncClient(timeout=httpx.Timeout(settings.vllm_timeout)) as client:
        r = await client.post(f"{base_url}/v1/chat/completions", json=payload,
                              headers={"Authorization": auth})
        if r.status_code != 200:
            raise RuntimeError(f"vLLM returned {r.status_code}: {r.text[:500]}")
        return r.json()


async def validate_extraction(prompt: str, system_prompt: str | None, model: str | None = None) -> dict:
    from app.core.config import settings as _s
    model = (model or _s.validation_model).strip()
    api_key = (settings.openai_api_key or "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY required for text validation.")
    api_root, _ = openai_urls(settings.openai_base_url)
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})
    payload: dict = {"model": model, "messages": messages, "temperature": 0.0, "max_tokens": 8192}
    async with httpx.AsyncClient(timeout=httpx.Timeout(settings.openai_timeout)) as client:
        r = await client.post(f"{api_root}/v1/chat/completions", json=payload,
                              headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"})
        if r.status_code != 200:
            raise RuntimeError(f"validation {r.status_code}: {r.text[:300]}")
        return r.json()
