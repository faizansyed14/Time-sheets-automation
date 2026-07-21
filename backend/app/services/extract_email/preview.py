"""LLM egress audit preview."""
from __future__ import annotations

import base64

from app.core.config import settings
from app.core.pii import scrub_email_for_llm, scrub_text
from app.models.email_message import EmailMessage
from app.services.extract_email.analyser import is_native_file_unit
from app.services.extract_email.collector import collect_units, merge_thread_units
from app.services.extract_email.prompts import extract_prompt

async def preview_llm_egress(
    email: EmailMessage,
    *,
    prior_email: EmailMessage | None = None,
) -> dict:
    """What Extract Email would send to the vision model AFTER PII redaction.

    Used by the Inbox "EML sent to LLM" preview — operators can audit
    subject/body/prompt text AND the exact stitched JPEGs (base64) that would
    be attached to the vision call. Body images are rendered after PII scrub.

    `prior_email`: mirrors extract_full_email's thread-awareness — when given,
    the preview shows sheets merged from BOTH thread messages (deduplicated),
    exactly what a real Extract Email run on this thread would send.
    """
    from app.services.email_provider import get_email_provider
    from app.services.inbox.eml_export import build_full_eml

    eml_bytes, _ = await build_full_eml(get_email_provider(), email)
    units = collect_units(email, eml_bytes)

    thread_sources: list[dict] | None = None
    if prior_email is not None:
        prior_eml_bytes, _ = await build_full_eml(get_email_provider(), prior_email)
        prior_units = collect_units(prior_email, prior_eml_bytes)
        units = merge_thread_units(units, prior_units)
        thread_sources = [
            {"provider_message_id": email.provider_message_id, "subject": email.subject},
            {"provider_message_id": prior_email.provider_message_id, "subject": prior_email.subject},
        ]

    from app.services.extraction import vision_client
    provider = vision_client.vision_provider()

    subj_sent, body_sent = scrub_email_for_llm(email.subject, email.body_text)
    sheets_out: list[dict] = []
    for u in units:
        is_body = u.name == "(email body)"
        is_native = is_native_file_unit(u, provider)
        # First (only) stitched JPEG that would go on the vision call — empty
        # for a native-file sheet (its raw bytes upload directly) or a
        # text-only body (no image rendered at all).
        img_b64 = ""
        if u.images:
            img_b64 = base64.b64encode(u.images[0]).decode("ascii")
        if is_native:
            note = ("The raw file itself is uploaded to OpenAI directly (no "
                    "client-side image render) — no JPEG is generated for this "
                    "sheet. The text below is sent alongside it as grounding.")
        elif is_body and not u.images:
            note = ("Email body sheet — sent as scrubbed TEXT only (no image "
                    "rendered): addresses, phones and secrets are tokenised "
                    "throughout the full thread (signatures included).")
        elif is_body:
            note = ("Email body sheet — the HTML contains a pasted table, so "
                    "it's rendered to a JPEG (preserves cell layout); addresses, "
                    "phones and secrets are tokenised throughout the full thread "
                    "BEFORE this JPEG is rendered and before prompt text.")
        else:
            note = ("One stitched JPEG of the full attachment (same bytes as the "
                    "vision call). Emp name/ID on timesheet sheets are intentional; "
                    "emails/phones in extracted text are scrubbed in the prompt.")
        sheets_out.append({
            "name": u.name,
            "file_type": u.ftype,
            "sent_as": "native_file" if is_native else ("image" if u.images else "text"),
            "image_pages": min(1, len(u.images or [])),
            "image_jpeg_b64": img_b64,
            "text_sent": (u.text or "")[:12000],
            "note": note,
        })

    sample_prompt = ""
    if units:
        u0 = units[0]
        sample_native = is_native_file_unit(u0, provider)
        sample_prompt = scrub_text(extract_prompt(email, u0, native=sample_native))

    return {
        "pii_redaction": bool(settings.pii_redaction),
        "scope": "full_email",
        "subject_sent": subj_sent,
        "body_sent": body_sent[:20000],
        "sender_omitted": True,
        "thread_sources": thread_sources,
        "sheets": sheets_out,
        "sample_prompt": sample_prompt[:24000],
        "system_prompt_note": "Extraction uses one prompt per sheet (no separate "
                             "system prompt; instructions are in the user prompt).",
        "omitted": [
            "Raw From / To / Cc header values (replaced with [header-redacted])",
            "Email addresses → person-******@redacted.invalid",
            "Phones (international / labelled T:/M:) → [phone-redacted]",
            "Password / secret lines → [secret-redacted]",
            "CID signature logos, named logo/banner/footer images, & wide "
            "marketing banners (never collected for OpenAI)",
            "Sender email (matched locally after the model call)",
            "Raw .eml bytes (never uploaded; only 1 stitched image per sheet + text)",
        ],
        "policy": (
            "Emp name and employee ID printed on timesheet PDFs/DOCX are "
            "allowed. Mailbox addresses, phones and credentials are tokenised "
            "throughout the full thread — signatures and quoted reply history "
            "are kept, not cut."
        ),
    }
