"""Extract Email inbox entry point — runs the agent orchestrator."""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.email_message import EmailMessage
from app.services.extract_email.results import build_result, staged_message
from app.services.extract_email.staging import mark_no_sheets


async def extract_full_email(
    db: AsyncSession, email: EmailMessage, *,
    prior_email: EmailMessage | None = None,
) -> dict:
    """Extract Email — the agent pipeline reads every sheet in the full .eml,
    resolves the employee, merges partial periods, checks duplicates and
    validation, then either auto-files or stages for review. The stored raw
    copy stays the full email for Compare & Fix.

    `prior_email`: only for approval-only replies — when the selected message
    has no document attachments, the immediately prior message in the same
    conversation is merged (deduplicated) so "Approved." can match the original
    timesheet. Messages with their own PDFs/DOCX are extracted alone.

    Returns {staged, groups, sheets, employees, approval, message}."""
    from app.services.email_provider import get_email_provider
    from app.services.extract_email.thread_collect import (
        build_thread_bundle, collect_thread_emls,
    )
    from app.services.extract_email.thread_extract import thread_call_available
    from app.services.inbox.eml_export import build_full_eml
    from app.services.orchestrator import (
        AgentContext, Orchestrator, build_pipeline, build_thread_pipeline,
    )

    provider = get_email_provider()

    # The WHOLE conversation goes to the model in one call — an approval that
    # arrived three replies later is only visible if the thread is sent whole.
    thread_messages, thread_notes = await collect_thread_emls(provider, email, prior_email)
    if thread_messages:
        # Store what the model actually read. Filing only the clicked message
        # would leave a record whose approval evidence lives in a reply nobody
        # can see from Compare & Fix or the vault.
        eml_bytes, eml_name = build_thread_bundle(thread_messages, email.subject)
    else:
        eml_bytes, eml_name = await build_full_eml(provider, email)

    ctx = AgentContext(
        db=db, source_kind="email", source_id=email.provider_message_id,
        # Extract Email's unit of work is the CONVERSATION, so review items
        # dedupe on it: a reply arriving later re-runs into the SAME item
        # instead of stacking a second one for the same employee+month.
        thread_key=(email.conversation_id or email.provider_message_id),
        source=email, raw_bytes=eml_bytes, raw_name=eml_name,
        content_type="message/rfc822", prior_source=prior_email,
        thread_messages=thread_messages,
    )
    ctx.notes.extend(thread_notes)
    # Without a usable model the one-call read cannot run at all. Rather than
    # return "nothing found" — which reads as "this email is empty" — fall back
    # to the per-sheet pipeline, whose local engine still extracts what it can.
    pipeline = build_thread_pipeline() if thread_call_available() else build_pipeline()
    await Orchestrator(pipeline).run(ctx)

    approval = ctx.approval or {"detected": False, "detail": "No approval check ran."}

    # Nothing usable — remember it on the email so the UI can say so lastingly.
    if not ctx.groups:
        if ctx.sheets:
            kinds = ", ".join(f"{s['name']} ({s['kind']})" for s in ctx.sheets)
            message = f"Nothing to stage — no timesheet or certificate found ({kinds})."
        else:
            message = "No readable sheets found inside this email."
        # A degraded thread fetch is the most important thing to surface right
        # here — "nothing found" reads very differently when it means "the
        # whole conversation was empty" versus "most of the conversation was
        # never actually read."
        if thread_notes:
            message = f"{message} " + " ".join(thread_notes[:3])
        await mark_no_sheets(db, email, message)
        return build_result([], [], ctx.sheets, approval, message)

    # This run DID find something — clear a stale "no sheets" mark.
    if email.no_sheets_found_at is not None:
        email.no_sheets_found_at = None
        email.no_sheets_note = None
        await db.commit()

    message = staged_message(ctx.groups, approval)
    if ctx.notes:
        message = f"{message} " + " ".join(ctx.notes[:3])
    return build_result(ctx.staged, ctx.groups, ctx.sheets, approval, message)
