"""Extract Email inbox entry point — runs the agent orchestrator."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.email_message import EmailMessage
from app.services.extract_email.results import build_result, staged_message
from app.services.extract_email.staging import mark_no_sheets


async def extract_full_email(
    db: AsyncSession, email: EmailMessage, *,
    prior_email: EmailMessage | None = None,
    force_full: bool = False,
    only_message_ids: list[str] | None = None,
) -> dict:
    """Serialised front door: one extraction per conversation at a time.

    Auto Extract, a duplicate task and a user's manual Extract click can all
    target the same conversation. They queue on a per-thread Redis lock
    instead of running side by side. A caller that had to WAIT re-checks the
    thread once it gets the lock and, if the run it waited for already covered
    every message, returns that run's result instead of paying for the same
    read again (unless `force_full` — an explicit "re-read everything")."""
    from app.services.extract_email.thread_lock import thread_lock

    thread_key = email.conversation_id or email.provider_message_id
    async with thread_lock(thread_key) as waited:
        if waited and not force_full and not only_message_ids:
            current = await _result_if_current(db, email, thread_key)
            if current is not None:
                return current
        return await _extract_locked(db, email, prior_email=prior_email, force_full=force_full,
                                     only_message_ids=only_message_ids)


async def _result_if_current(db: AsyncSession, email: EmailMessage, thread_key: str) -> dict | None:
    """The finished result of a run that already covered the whole thread, or
    None if there is anything newer / the earlier run was incomplete."""
    from sqlalchemy import func, select

    from app.models.pipeline_file import PipelineFile
    from app.services.extract_email import sheet_cache
    from app.services.extract_email.constants import TAG_PREFIX

    newest = (await db.execute(
        select(func.max(EmailMessage.received_at)).where(
            func.coalesce(EmailMessage.conversation_id, EmailMessage.provider_message_id)
            == thread_key))).scalar_one_or_none()
    rows = (await db.execute(select(PipelineFile).where(
        PipelineFile.source_kind == "email", PipelineFile.thread_key == thread_key,
        PipelineFile.attachment_id.like(f"{TAG_PREFIX}%")))).scalars().all()
    watermark = await sheet_cache.last_extraction_at(db, thread_key)
    note = "Already extracted by a run that just finished - nothing new to read."
    if rows and watermark is not None and (newest is None or watermark >= newest):
        fe0 = (rows[0].extraction_meta or {}).get("full_email_extract") or {}
        sheets = [{"name": x.get("filename"), "kind": x.get("kind"),
                   "employee_name": x.get("employee_name")}
                  for r in rows
                  for x in ((r.extraction_meta or {}).get("full_email_extract") or {}).get("sheets") or []]
        groups = [{"name": r.employee_name, "sheets": []} for r in rows]
        return build_result(
            rows, groups, sheets,
            fe0.get("approval") or {"detected": False, "detail": ""}, note)
    await db.refresh(email)
    if (email.no_sheets_found_at is not None and newest is not None
            and email.no_sheets_found_at >= newest and not rows):
        return build_result([], [], [], {"detected": False, "detail": ""},
                            email.no_sheets_note or note)
    return None


async def _extract_locked(
    db: AsyncSession, email: EmailMessage, *,
    prior_email: EmailMessage | None = None,
    force_full: bool = False,
    only_message_ids: list[str] | None = None,
) -> dict:
    """Extract Email — the agent pipeline reads every sheet in the full .eml,
    resolves the employee, merges partial periods, checks duplicates and
    validation, then either auto-files or stages for review. The stored raw
    copy stays the full email for Compare & Fix.

    `prior_email`: only for approval-only replies — when the selected message
    has no document attachments, the immediately prior message in the same
    conversation is merged (deduplicated) so "Approved." can match the original
    timesheet. Messages with their own PDFs/DOCX are extracted alone.

    `only_message_ids`: read ONLY these messages of the conversation (the reviewer ticked
    them). They are read fresh; everything already extracted from the other messages is
    kept; the stored copy of the conversation is still the whole thread; the thread's
    "read up to here" mark is NOT advanced (other messages were not read) and no other
    item is removed.

    `force_full`: bypass incremental windowing/attachment-cache reuse and
    re-read the ENTIRE conversation fresh — the "Select all -> Extract" escape
    hatch, for when a stale/wrong past read is suspected. Off by default: an
    already-extracted thread only re-fetches the new message(s) + a couple of
    prior ones for context (see thread_collect.collect_thread_emls' `since`),
    reusing everything else already extracted for this conversation.

    Returns {staged, groups, sheets, employees, approval, message}."""
    from app.services.email_provider import get_email_provider
    from app.services.extract_email import sheet_cache
    from app.services.extract_email.thread_collect import (
        build_thread_bundle, collect_thread_emls,
    )
    from app.services.extract_email.thread_extract import require_vision_configured
    from app.services.inbox.eml_export import build_full_eml
    from app.services.orchestrator import AgentContext, Orchestrator, build_thread_pipeline

    # Fail loudly and immediately rather than silently reporting "no sheets
    # found" — there is no fallback pipeline, a vision model is mandatory.
    require_vision_configured()

    provider = get_email_provider()
    thread_key = email.conversation_id or email.provider_message_id

    # When this thread has been extracted before, only fetch what's new (plus
    # a couple of messages of context) — not the whole conversation again.
    selective = bool(only_message_ids)
    since = None if (force_full or selective) else await sheet_cache.last_extraction_at(db, thread_key)

    # Captured BEFORE the fetch — the true "as of" cutoff for this run (see
    # AgentContext.snapshot_at's own docstring for why this, and not the
    # PipelineFile row's updated_at, is what a later re-check must compare
    # a new message's received_at against).
    snapshot_at = datetime.now(timezone.utc)
    if selective:
        # Not a full read: keep the thread's previous "read up to here" mark (or epoch if it
        # never had one) so unread newer messages are still found by the next scan.
        prev = await sheet_cache.last_extraction_at(db, thread_key)
        snapshot_at = prev or datetime(1970, 1, 1, tzinfo=timezone.utc)

    # The relevant WINDOW of the conversation goes to the model in one call —
    # an approval that arrived three replies later is only visible if that
    # reply (and enough of what it's replying to) is sent together.
    thread_messages, thread_notes = await collect_thread_emls(
        provider, email, prior_email, since=since,
        only_ids=set(only_message_ids) if selective else None)
    raw_complete = True
    if thread_messages:
        # The stored copy is the WHOLE conversation, not just what the model was
        # shown this time. A reply into an already-extracted thread only has the new
        # message(s) re-read (the rest comes from the per-attachment record), but the
        # evidence kept with the review item - and later filed in the vault - must
        # still hold every message and attachment, or each reply would shrink it to
        # the last few messages.
        store_messages = thread_messages
        if since is not None or selective:
            full_messages, full_notes = await collect_thread_emls(
                provider, email, prior_email, since=None)
            if (full_messages and not getattr(full_notes, "problems", [])
                    and len(full_messages) >= len(thread_messages)):
                store_messages = full_messages
            else:
                # Could not rebuild the whole conversation right now: keep the copy
                # already stored rather than replace it with a shorter one.
                raw_complete = False
                thread_notes.append(
                    "The stored copy of this conversation could not be refreshed with every "
                    "message just now - the previously stored copy was kept. Tick the messages "
                    "(Select all) and press Extract once the mailbox is reachable.")
        eml_bytes, eml_name = build_thread_bundle(store_messages, email.subject)
    else:
        eml_bytes, eml_name = await build_full_eml(provider, email)

    ctx = AgentContext(
        db=db, source_kind="email", source_id=email.provider_message_id,
        # Extract Email's unit of work is the CONVERSATION, so review items
        # dedupe on it: a reply arriving later re-runs into the SAME item
        # instead of stacking a second one for the same employee+month.
        thread_key=thread_key,
        source=email, raw_bytes=eml_bytes, raw_name=eml_name,
        content_type="message/rfc822", prior_source=prior_email,
        thread_messages=thread_messages, force_full=force_full,
        snapshot_at=snapshot_at, raw_complete=raw_complete,
        only_message_ids=list(only_message_ids or []),
    )
    ctx.notes.extend(thread_notes)
    if selective:
        ctx.notes.append(
            f"Only the {len(thread_messages)} selected message(s) were read; what was already "
            "extracted from the other messages was kept.")
    ctx.fetch_problems = list(getattr(thread_notes, "problems", []) or [])
    await Orchestrator(build_thread_pipeline()).run(ctx)

    run_errors = [str(e) for e in ((ctx.run_meta or {}).get("errors") or [])]
    incomplete = bool((ctx.run_meta or {}).get("incomplete")) or bool(ctx.fetch_problems)
    flags = {"incomplete": incomplete, "errors": run_errors}

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
        # ctx.notes (e.g. "looks like a multi-employee roster, use Bulk Upload
        # instead") is normally only surfaced on the success path below — but
        # an email whose ONLY attachment was a skipped roster ends up here
        # instead, and that's exactly when the reviewer most needs the note.
        if ctx.notes:
            message = f"{message} " + " ".join(ctx.notes[:3])
        if incomplete:
            # Part of the conversation could not be read - "nothing found" would
            # be a lie, and marking the thread as checked would stop every later
            # scan from ever looking again. Leave it unmarked so it is retried.
            message = ("Extraction INCOMPLETE - nothing could be read yet: "
                       + "; ".join(run_errors[:3]) + ". It will be retried.")
        else:
            await mark_no_sheets(db, email, message)
        return {**build_result([], [], ctx.sheets, approval, message), **flags}

    # This run DID find something — clear a stale "no sheets" mark.
    if email.no_sheets_found_at is not None:
        email.no_sheets_found_at = None
        email.no_sheets_note = None
        await db.commit()

    message = staged_message(ctx.groups, approval)
    if incomplete:
        message = (f"{message} INCOMPLETE - " + "; ".join(run_errors[:3])
                   + ". It will be retried.")
    if ctx.notes:
        message = f"{message} " + " ".join(ctx.notes[:3])
    return {**build_result(ctx.staged, ctx.groups, ctx.sheets, approval, message), **flags}
