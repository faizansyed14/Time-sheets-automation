"""
Vision LLM plumbing for Extract Email (app.services.extract_email.analyser).

Provider is settings.llm_provider ("openai" or any OpenAI-compatible gateway,
e.g. "openrouter" — see vision_provider()):

  - vision_provider / model_for / openai_api_key: provider + model routing.
  - _openai_by_images: base64 JPEG + text via chat completions — works against
    any OpenAI-compatible endpoint unchanged.
  - _openai_by_files: OpenAI-only — uploads PDF/DOCX/XLSX via Responses API.
  - _openai_thread_call: ONE call carrying a whole email thread — every
    attachment plus every image plus the scrubbed bodies. On "openai" this
    uploads files natively via the Responses API; on any other provider it
    delegates to _chat_thread_call, which renders PDF/DOCX/XLSX to page
    images first (no file-storage equivalent exists for a third-party model
    routed through a gateway) and sends everything as one chat-completions
    call instead.

Prompts are PII-scrubbed (core/pii.py) before every call.
Leave/date flags and review summaries are deterministic (validation.py) —
there is no second LLM cross-check.
"""
from __future__ import annotations

import base64
import re

import httpx

from app.core.config import settings
from app.core.openai_url import openai_urls
from app.core.pii import scrub_text

# File types OpenAI's Responses API can ingest directly (no client-side
# render). docx/xlsx go up as-is — OpenAI's own document pipeline reads them;
# we still append our own extracted text (incl. xlsx cell-colour legend
# annotations) alongside, since that grounding is what makes colour-coded
# sheets readable — dropping it silently loses that data (measured).
NATIVE_FILE_TYPES = ("pdf", "docx", "xlsx")
_FILE_CONTENT_TYPES = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}
_FILE_EXT = {"pdf": ".pdf", "docx": ".docx", "xlsx": ".xlsx"}


def vision_provider() -> str:
    """"openai" gets the native Files/Responses path; anything else (e.g.
    "openrouter") is a plain chat-completions gateway — see _openai_thread_call."""
    return (settings.llm_provider or "openai").strip().lower()


def model_for(provider: str, purpose: str = "vision") -> str:
    """The OpenAI vision model to send. `purpose` kept for call-site compatibility."""
    del provider, purpose
    return (settings.openai_vision_model or "gpt-4o").strip()


def openai_api_key() -> str:
    return (settings.openai_api_key or "").strip()


async def _openai_by_images(images_jpeg, prompt, system_prompt, model,
                            image_detail, api_key) -> dict:
    """OpenAI-native chat call: base64 JPEG page images + text, with strict
    JSON mode on models that support it."""
    prompt = scrub_text(prompt)
    api_root, _ = openai_urls(settings.openai_base_url)
    detail = (image_detail or "low").strip().lower()
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
        if getattr(settings, "vision_json_mode", True) and model.lower().startswith(("gpt-4o", "gpt-4.1")):
            payload["response_format"] = {"type": "json_object"}
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=httpx.Timeout(settings.openai_timeout)) as client:
        r = await client.post(f"{api_root}/v1/chat/completions", json=payload, headers=headers)
        if r.status_code != 200:
            raise RuntimeError(f"OpenAI returned {r.status_code}: {r.text[:500]}")
        return r.json()


def _safe_upload_name(name: str, ftype: str) -> str:
    """A filename OpenAI accepts that still identifies the sheet.

    The model is told to key its per-sheet results by these names, so they must
    be distinct and recognisable — uploading everything as "sheet.pdf" makes it
    impossible to tell whose timesheet is whose in a multi-attachment thread.
    """
    ext = _FILE_EXT.get(ftype, ".pdf")
    stem = re.sub(r"[^A-Za-z0-9._ -]+", "_", (name or "sheet")).strip() or "sheet"
    if stem.lower().endswith(ext):
        stem = stem[: -len(ext)]
    return f"{stem[:96]}{ext}"


async def _chat_thread_call(
    files, images, prompt, system_prompt, model, api_key, image_detail="high",
) -> dict:
    """Chat-completions equivalent of the Responses-API thread call, for any
    OpenAI-compatible gateway that fronts third-party models (OpenRouter,
    etc.) — these expose /v1/chat/completions but have no file store to
    upload a PDF into, so native files are rendered to page images first and
    everything (files-as-images + real images) goes up as one user message.
    """
    from app.services.extraction.file_processor import to_images

    prompt = scrub_text(prompt)
    api_root, _ = openai_urls(settings.openai_base_url)
    content: list[dict] = []

    for name, payload, ftype in files:
        try:
            rendered = to_images(ftype, payload)
        except Exception:
            rendered = []
        content.append({"type": "text", "text": f"--- ATTACHMENT: {name} ---"})
        for img in rendered:
            b64 = base64.b64encode(img).decode("utf-8")
            content.append({"type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{b64}",
                                          "detail": image_detail}})

    for name, payload in images:
        b64 = base64.b64encode(payload).decode("utf-8")
        content.append({"type": "text", "text": f"--- IMAGE: {name} ---"})
        content.append({"type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{b64}",
                                      "detail": image_detail}})

    content.append({"type": "text", "text": prompt})
    messages = []
    if (system_prompt or "").strip():
        messages.append({"role": "system", "content": system_prompt.strip()})
    messages.append({"role": "user", "content": content})

    payload_req: dict = {"model": model, "messages": messages,
                        "temperature": 0.0, "max_tokens": 8192}
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=httpx.Timeout(settings.openai_timeout)) as client:
        r = await client.post(f"{api_root}/v1/chat/completions", json=payload_req, headers=headers)
        if r.status_code != 200:
            raise RuntimeError(f"{api_root} returned {r.status_code}: {r.text[:500]}")
        return r.json()


async def _openai_thread_call(
    files, images, prompt, system_prompt, model, api_key, image_detail="high",
) -> dict:
    """ONE call carrying an entire email thread — routes on vision_provider().

    `files`  : [(display_name, payload, ftype)] — PDF/DOCX/XLSX, each under its
               own name so the model can attribute results.
    `images` : [(display_name, jpeg_or_png_bytes)] — screenshots, pasted grids,
               signature logos: everything, so the model decides what matters
               instead of a heuristic deciding for it.

    "openai" uploads `files` natively via the Responses API. Any other
    provider has no equivalent file store to upload into, so `files` are
    rendered to page images first and everything goes up as one chat-
    completions call (_chat_thread_call) instead.

    Returns the chat-completions-shaped dict the existing JSON parser expects.
    """
    if vision_provider() != "openai":
        return await _chat_thread_call(
            files, images, prompt, system_prompt, model, api_key, image_detail)
    prompt = scrub_text(prompt)
    api_root, _ = openai_urls(settings.openai_base_url)
    file_ids: list[str] = []
    async with httpx.AsyncClient(timeout=httpx.Timeout(settings.openai_timeout)) as client:
        try:
            content: list[dict] = []
            for name, payload, ftype in files:
                ctype = _FILE_CONTENT_TYPES.get(ftype, "application/pdf")
                up = await client.post(
                    f"{api_root}/v1/files",
                    headers={"Authorization": f"Bearer {api_key}"},
                    files={"file": (_safe_upload_name(name, ftype), payload, ctype)},
                    data={"purpose": "user_data"})
                if up.status_code != 200:
                    raise RuntimeError(f"OpenAI file upload {up.status_code}: {up.text[:500]}")
                fid = up.json()["id"]
                file_ids.append(fid)
                # Label each attachment so the JSON can name its source sheet.
                content.append({"type": "input_text", "text": f"--- ATTACHMENT: {name} ---"})
                content.append({"type": "input_file", "file_id": fid})

            for name, payload in images:
                b64 = base64.b64encode(payload).decode("utf-8")
                content.append({"type": "input_text", "text": f"--- IMAGE: {name} ---"})
                content.append({
                    "type": "input_image",
                    "image_url": f"data:image/jpeg;base64,{b64}",
                    "detail": image_detail,
                })

            content.append({"type": "input_text", "text": prompt})
            req: dict = {"model": model, "input": [{"role": "user", "content": content}]}
            if (system_prompt or "").strip():
                req["instructions"] = system_prompt.strip()
            r = await client.post(
                f"{api_root}/v1/responses", json=req,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"})
            if r.status_code != 200:
                raise RuntimeError(f"OpenAI returned {r.status_code}: {r.text[:500]}")
            body = r.json()
            text_out = "".join(
                c.get("text", "") for item in body.get("output", [])
                for c in item.get("content", []) if c.get("type") == "output_text")
            return {"choices": [{"message": {"content": text_out}}], "usage": body.get("usage", {})}
        finally:
            for fid in file_ids:
                try:
                    await client.delete(f"{api_root}/v1/files/{fid}",
                                        headers={"Authorization": f"Bearer {api_key}"})
                except Exception:
                    pass


async def _openai_by_files(files, prompt, system_prompt, model, api_key) -> dict:
    """OpenAI-native, no client-side image render: upload each sheet's raw
    bytes (PDF/DOCX/XLSX) and reference them via the Responses API, alongside
    the same grounding text `prompt` already carries.

    Returns a dict shaped like a chat-completions response so the existing
    extract_json_from_llm_response() parser needs no changes."""
    prompt = scrub_text(prompt)
    api_root, _ = openai_urls(settings.openai_base_url)
    file_ids: list[str] = []
    async with httpx.AsyncClient(timeout=httpx.Timeout(settings.openai_timeout)) as client:
        try:
            for payload, ftype in files:
                ext = _FILE_EXT.get(ftype, ".pdf")
                ctype = _FILE_CONTENT_TYPES.get(ftype, "application/pdf")
                up = await client.post(
                    f"{api_root}/v1/files",
                    headers={"Authorization": f"Bearer {api_key}"},
                    files={"file": (f"sheet{ext}", payload, ctype)},
                    data={"purpose": "user_data"})
                if up.status_code != 200:
                    raise RuntimeError(f"OpenAI file upload {up.status_code}: {up.text[:500]}")
                file_ids.append(up.json()["id"])

            content: list[dict] = [{"type": "input_file", "file_id": fid} for fid in file_ids]
            content.append({"type": "input_text", "text": prompt})
            req: dict = {"model": model, "input": [{"role": "user", "content": content}]}
            if (system_prompt or "").strip():
                req["instructions"] = system_prompt.strip()
            r = await client.post(
                f"{api_root}/v1/responses", json=req,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"})
            if r.status_code != 200:
                raise RuntimeError(f"OpenAI returned {r.status_code}: {r.text[:500]}")
            body = r.json()
            text_out = "".join(
                c.get("text", "") for item in body.get("output", [])
                for c in item.get("content", []) if c.get("type") == "output_text")
            return {"choices": [{"message": {"content": text_out}}], "usage": body.get("usage", {})}
        finally:
            for fid in file_ids:
                try:
                    await client.delete(f"{api_root}/v1/files/{fid}",
                                        headers={"Authorization": f"Bearer {api_key}"})
                except Exception:
                    pass
