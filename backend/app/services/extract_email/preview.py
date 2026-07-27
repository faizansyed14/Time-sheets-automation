"""Exactly what Extract Email sends to OpenAI, and in what order.

Powers the Inbox "EML sent to LLM" audit view. Everything here is built the
SAME way the real run builds it — same thread collection, same PII scrub, same
prompt — so what is shown is what would actually leave the building, not a
description of it.

Honest about the boundary: message bodies are scrubbed, attachments are NOT.
A PDF is uploaded byte-for-byte, so whatever is printed on the sheet goes with
it. The view says so rather than implying everything is redacted.
"""
from __future__ import annotations

import base64

from app.core.config import settings
from app.models.email_message import EmailMessage

_SKIP_REASON_LABELS = {
    "over_capacity": "too many attachments for one call",
    "too_small": "under 30 KB — treated as a logo/icon, not sent",
    "unsupported_type": "unsupported type",
}


async def preview_llm_egress(
    email: EmailMessage,
    *,
    prior_email: EmailMessage | None = None,
    db=None,
) -> dict:
    """A step-by-step account of the single thread call.

    Returns the ordered steps, the exact parts that would be attached, the
    verbatim prompts, and what is redacted vs what is not.
    """
    from app.services.email_provider import get_email_provider
    from app.services.extract_email.thread_collect import collect_thread_emls
    from app.services.extract_email.thread_extract import collect_thread_payload
    from app.services.extract_email.triage_prompt import build_triage_prompt
    from app.services.extraction import vision_client

    provider = get_email_provider()
    messages, thread_notes = await collect_thread_emls(provider, email, prior_email)
    payload = collect_thread_payload(messages)

    model = vision_client.model_for(vision_client.vision_provider(), "vision")

    # Every attachment is read on every run — nothing is reused.
    files_sent = [
        {"name": name, "file_type": ftype, "bytes": len(data),
         "sha256": payload.digests.get(name, "")[:16]}
        for name, data, ftype in payload.files
    ]
    images_sent = [
        {"name": name, "file_type": "image", "bytes": len(data),
         "sha256": payload.digests.get(name, "")[:16],
         "jpeg_b64": base64.b64encode(data).decode("ascii")}
        for name, data in payload.images
    ]

    # Pass 1's prompt is the one that carries the whole payload, so it is what
    # this view shows verbatim. Pass 2 is built from pass 1's ANSWER, which
    # does not exist until the call runs — described in the steps instead.
    system_prompt, user_prompt = build_triage_prompt(
        manifest=payload.manifest, bodies=payload.bodies)

    n_files, n_images = len(files_sent), len(images_sent)
    steps = [
        {
            "n": 1,
            "title": "Collect the whole conversation",
            "detail": (f"{len(messages)} message(s) fetched from the mail provider as raw "
                       ".eml, oldest first — not just the message you are looking at. An "
                       "approval that arrived in a later reply is only visible if the "
                       "whole thread goes together."),
            "items": [label for label, _ in messages],
        },
        {
            "n": 2,
            "title": "Strip personal data from the message text",
            "detail": ("Email addresses, phone numbers and credentials are replaced in "
                       "every body, signatures and quoted history included. Names, "
                       "employee IDs, dates and hours are deliberately KEPT — the "
                       "employee matcher needs them."),
            "items": [f"{len(payload.bodies)} characters of scrubbed body text"],
        },
        {
            "n": 3,
            "title": "Identify the client template (no AI)",
            "detail": ("Each attachment's own text is matched against known client "
                       "templates by marker scoring. Only the matching template's rules "
                       "are pasted into the prompt, so an ADR sheet is never shown DEWA "
                       "rules."),
            "items": payload.format_ids or ["no known template matched — generic rules"],
        },
        {
            "n": 4,
            "title": f"Upload {n_files} file(s) to OpenAI",
            "detail": ("PDF / Word / Excel are uploaded through the Files API and "
                       "referenced in the call. These are sent BYTE-FOR-BYTE and are NOT "
                       "redacted — whatever is printed on the sheet goes with it. They "
                       "are deleted from OpenAI immediately after the reply."
                       if n_files else "No document attachments in this thread."),
            "items": [f"{f['name']} ({f['bytes'] // 1024} KB)" for f in files_sent] or ["—"],
        },
        {
            "n": 5,
            "title": f"Inline {n_images} image(s)",
            "detail": ("Images are embedded in the request as base64 — signature logos "
                       "included, so the model decides what is a pasted approval "
                       "screenshot instead of a size threshold deciding for it. A pasted "
                       "HTML grid is rendered AFTER scrubbing, so redacted values are "
                       "pixels."
                       if n_images else "No images in this thread."),
            "items": [f"{i['name']} ({i['bytes'] // 1024} KB)" for i in images_sent] or ["—"],
        },
        {
            "n": 6,
            "title": f"PASS 1 of 2 — {model} reads the whole conversation",
            "detail": ("Everything above goes up in one request. This pass does NOT "
                       "extract leave — it works out which items are really timesheets, "
                       "whose each one is, which template it follows, whether a manager "
                       "approved (signature on the sheet, a screenshot, or wording in the "
                       "thread), and what the conversation is about. Logos and banners "
                       "are named as noise here so they never reach extraction."),
            "items": [f"1 call · {n_files} file(s) · {n_images} image(s)",
                      "returns: items[] · approval{} · summary{} · noise[]"],
        },
        {
            "n": 7,
            "title": f"PASS 2 of 2 — {model} extracts the confirmed sheets",
            "detail": ("Only the sheets pass 1 validated are sent again, with a prompt "
                       "that does nothing but transcribe names and leave dates — "
                       "including the messy cases: partial months, missing days, empty "
                       "columns, two-digit years. Splitting the work is deliberate: one "
                       "call asked to both classify and transcribe did neither well."),
            "items": ["1 call · only the confirmed timesheets/certificates",
                      "returns: sheets[] with per-date leave"],
        },
        {
            "n": 8,
            "title": "Then everything else happens locally",
            "detail": ("Employee matching against your HR master, validation, duplicate "
                       "checks and the auto-accept decision all run on this server. The "
                       "model never sees your employee list."),
            "items": ["match → validate → duplicate check → auto-accept or review"],
        },
    ]

    return {
        "flow": "thread-two-pass",
        "model": model,
        "pii_redaction": bool(settings.pii_redaction),
        "scope": "full_thread",
        "steps": steps,
        "thread_messages": [label for label, _ in messages],
        # Non-empty when the mailbox fetch degraded (fewer messages sent than
        # the reviewer would assume) — surfaced here so the audit view can
        # explain a thin result instead of leaving it a mystery.
        "warnings": thread_notes,
        "subject_sent": (email.subject or ""),
        "body_sent": payload.bodies[:40000],
        "files_sent": files_sent,
        "images_sent": images_sent,
        "not_sent": [
            f"{name} ({_SKIP_REASON_LABELS.get(reason, 'unsupported type')})"
            for name, reason in payload.skipped
        ],
        "formats_detected": payload.format_ids,
        "system_prompt": system_prompt,
        "user_prompt": user_prompt[:40000],
        "call_count": {
            # Pass 1 (understand) + pass 2 (extract). Pass 2 only runs when
            # pass 1 actually confirmed a sheet, so a thread with nothing in it
            # costs one call, not two.
            "inference": 2,
            "file_uploads": n_files,
            "file_deletes": n_files,
        },
        "redacted": [
            "Email addresses → person-******@redacted.invalid",
            "Phone numbers (international / labelled T:/M:) → [phone-redacted]",
            "Passwords / secrets → [secret-redacted]",
            "Raw From / To / Cc header values → [header-redacted]",
        ],
        "not_redacted": [
            "Employee names and employee IDs (the matcher needs them)",
            "Dates, hours and leave types",
            "ATTACHMENT CONTENTS — PDFs / Word / Excel are uploaded byte-for-byte, "
            "so anything printed on the sheet (name, ID, signature) goes with it",
        ],
        "policy": (
            "Message text is scrubbed; attachments are not. The whole conversation is "
            "sent in ONE call so an approval in a later reply can be matched to the "
            "sheet it approves. Uploaded files are deleted from OpenAI right after the "
            "reply, and your employee list is never sent."
        ),
    }
