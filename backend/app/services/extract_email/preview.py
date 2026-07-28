"""Exactly what Extract Email sends to the vision model, and in what order.

Powers the Inbox "EML sent to LLM" audit view. Everything here is built the
SAME way the real run builds it — same thread collection, same PII scrub,
same prompt — so what is shown is what would actually leave the building, not
a description of it.

Honest about the boundary: message bodies are scrubbed, attachments are NOT.
A rendered page image carries whatever is printed on the sheet. The view says
so rather than implying everything is redacted.
"""
from __future__ import annotations

import base64

from app.core.config import settings
from app.models.email_message import EmailMessage


async def preview_llm_egress(
    email: EmailMessage,
    *,
    prior_email: EmailMessage | None = None,
    db=None,
) -> dict:
    """A step-by-step account of the two-pass thread call.

    Returns the ordered steps, the exact items that would be sent, the
    verbatim pass-1 prompt, and what is redacted vs what is not.
    """
    from app.services.email_provider import get_email_provider
    from app.services.extract_email.thread_collect import collect_thread_emls
    from app.services.extract_email.thread_extract import collect_thread
    from app.services.extract_email.triage_prompt import (
        build_bodies_block, build_item_manifest, chunk_items_by_count,
    )
    from app.services.extraction import vision_client

    provider = get_email_provider()
    messages, thread_notes = await collect_thread_emls(provider, email, prior_email)
    th = collect_thread(messages)

    model = vision_client.model_for(vision_client.vision_provider(), "vision")

    files_sent = [
        {"name": f"[{it.key}] {it.name}", "file_type": it.send_mode,
         "bytes": it.size or len(it.text or ""), "sha256": (it.digest or "")[:16]}
        for it in th.items
    ]
    images_sent = [
        {"name": f"[{it.key}] {it.name} — page {pi} of {len(it.images)}",
         "file_type": "image", "bytes": len(img), "sha256": (it.digest or "")[:16],
         "jpeg_b64": base64.b64encode(img).decode("ascii")}
        for it in th.items for pi, img in enumerate(it.images, 1)
    ]

    batches = chunk_items_by_count(th.items, settings.pass1_batch_size)
    first_batch = batches[0] if batches else []

    # Pass 1's prompt carries the whole payload (or the first batch of it on a
    # long thread), so it is what this view shows verbatim. Pass 2 is built
    # from pass 1's ANSWER, which does not exist until the call runs —
    # described in the steps instead.
    system_prompt = None
    user_manifest = build_item_manifest(first_batch)
    user_bodies = build_bodies_block(th)
    from app.services.extract_email.triage_prompt import PASS1_OUTPUT, PASS1_SYSTEM, PASS1_USER_RULES
    system_prompt = PASS1_SYSTEM
    user_prompt = "\n\n".join([user_manifest, user_bodies, PASS1_USER_RULES, PASS1_OUTPUT])

    n_items, n_images = len(th.items), th.n_images
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
            "items": [f"{len(user_bodies)} characters of scrubbed body text"],
        },
        {
            "n": 3,
            "title": "Classify by structure, not a template catalogue",
            "detail": ("There is no per-client template list any more — pass 1 reasons "
                       "from what it sees (repeating day-by-day rows vs a handful of "
                       "leave records vs bare approval wording), the same way a human "
                       "reviewer who has never seen this exact form before would."),
            "items": ["no format registry — structural classification only"],
        },
        {
            "n": 4,
            "title": f"Render {n_items} item(s), {n_images} page image(s)",
            "detail": ("Every attachment AND every message body becomes its own labelled "
                       "[A#] item. PDFs/DOCX/XLSX render to page images (one JPEG per "
                       "page); a standalone picture attachment is OCR-screened first — "
                       "under-threshold text drops it as a logo/icon before any API call "
                       "is spent on it. Text content (native PDF text, xlsx/docx text, "
                       "csv/txt) travels alongside as supplementary grounding."),
            "items": [f"{f['name']} ({f['file_type']}, {f['bytes'] // 1024} KB)"
                      for f in files_sent] or ["—"],
        },
        {
            "n": 5,
            "title": f"PASS 1 — {model} classifies every item"
                     + (f" ({len(batches)} call(s), batched by item count)" if len(batches) > 1 else ""),
            "detail": ("This pass does NOT extract leave — it decides which items are "
                       "really timesheets, whose each one is, whether a manager approved "
                       "(signature on the sheet, a screenshot, or wording in the thread), "
                       "and writes the one plain-English thread summary."),
            "items": [f"{len(batches)} call(s) · {n_items} item(s) total",
                      "returns: thread_summary · items[] (kind, employee, evidence, approval signal)"],
        },
        {
            "n": 6,
            "title": "PASS 2 — reads only the confirmed sheets, full day-by-day account",
            "detail": ("Only the items pass 1 confirmed as timesheet/leave_certificate are "
                       "sent again, batched by IMAGE COUNT this time. This prompt "
                       "transcribes not just leave but every day of the period: worked, "
                       "weekend, a leave type, or genuinely uncertain — a complete, "
                       "checkable account, not just a scan for leave."),
            "items": ["batched by image count (MAX_IMAGES_PER_CALL)",
                      "returns: sheets[] with working_days/weekend_days/leave buckets/uncertain_days"],
        },
        {
            "n": 7,
            "title": "Then everything else happens locally",
            "detail": ("Employee matching against your HR master, date/conflict "
                       "validation, duplicate checks and the auto-accept decision all run "
                       "on this server. The model never sees your employee list. "
                       "Auto-accept only recommends a group when every day is confidently "
                       "accounted for — no unaccounted days, no model-flagged uncertainty."),
            "items": ["match → normalise/validate → duplicate check → auto-accept or review"],
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
        "body_sent": user_bodies[:40000],
        "files_sent": files_sent,
        "images_sent": images_sent,
        "not_sent": [
            f"{d.get('name')} ({d.get('reason', 'filtered')})"
            for d in th.dropped
        ],
        "formats_detected": [],
        "system_prompt": system_prompt,
        "user_prompt": user_prompt[:40000],
        "call_count": {
            # Pass 1 (classify) call count is known up front; pass 2 (extract)
            # only runs on whatever pass 1 confirms, so its count isn't known
            # until pass 1's answer exists — not represented here.
            "inference": len(batches),
            "file_uploads": 0,   # everything travels inline (base64) — no file-upload API call
            "file_deletes": 0,
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
            "ATTACHMENT CONTENTS — rendered page images and native text carry "
            "whatever is printed on the sheet, including any name/ID/signature",
        ],
        "policy": (
            "Message text is scrubbed; attachments are not. Pass 1 sees the whole "
            "conversation (batched on a long thread); pass 2 only reads what pass 1 "
            "confirmed. Your employee list is never sent."
        ),
    }
