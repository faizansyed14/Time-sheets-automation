"""Upload and chat extraction entry points.

Upload runs the SAME two-pass reader as Extract Email — one call to understand
the whole submission, one to extract the confirmed sheets — falling back to
the older per-sheet engine pipeline only when no vision model is configured
(mirrors app.services.extract_email.email.extract_full_email exactly)."""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.extract_email.collector import collect_units, unit_from_bytes
from app.services.extract_email.results import build_result, staged_message
from app.services.extract_email.types import SheetUnit, SourceCtx

def units_from_upload(filename: str, data: bytes) -> tuple[SourceCtx, list[SheetUnit]]:
    """Uploaded bytes → (context, units). An uploaded .eml unpacks exactly like
    Extract Email (attachments + forwarded emails + body); anything else is one
    sheet. Only used by the per-sheet FALLBACK pipeline (no vision model
    configured) — see as_thread_messages() for the normal path."""
    from app.services.extraction.file_processor import detect_file_type

    ftype = detect_file_type(filename, data)
    if ftype == "eml":
        import email as email_lib
        from email import policy
        subject, body_text = filename, None
        try:
            msg = email_lib.message_from_bytes(data, policy=policy.default)
            subject = str(msg["subject"] or filename)
            body = msg.get_body(preferencelist=("plain",))
            body_text = body.get_content() if body else None
        except Exception:
            pass
        ctx = SourceCtx(subject=subject, body_text=body_text)
        return ctx, collect_units(ctx, data)
    ctx = SourceCtx(subject=filename)
    if not ftype:
        return ctx, []
    u = unit_from_bytes(filename, ftype, data)
    return ctx, [u] if u else []


def _wrap_as_single_attachment_eml(filename: str, data: bytes) -> bytes:
    """Wrap a bare uploaded file — a PDF/XLSX/DOCX/image with no email envelope
    — as a minimal one-attachment message, so it reaches the two-pass reader
    (collect_thread_payload parses real RFC822 bytes) the same way a real .eml
    upload does. No body text — the file itself is the submission."""
    from email.message import EmailMessage as MimeMessage

    from app.services.extraction.file_processor import content_type_for

    # Default policy (not compat32) — set_content/add_attachment need its
    # content_manager. thread_extract.py parses with compat32 afterwards,
    # which reads either policy's wire format identically.
    msg = MimeMessage()
    msg["Subject"] = filename
    msg.set_content("")
    ctype = content_type_for(filename, data)
    maintype, _, subtype = ctype.partition("/")
    msg.add_attachment(data, maintype=maintype or "application",
                       subtype=subtype or "octet-stream", filename=filename)
    return msg.as_bytes()


def as_thread_messages(filename: str, data: bytes) -> list[tuple[str, bytes]]:
    """One uploaded file → one "thread message", for the same two-pass reader
    Extract Email uses. A real .eml upload already carries everything the
    reader needs (body, real attachments, any forwarded emails); a bare file
    is wrapped first so both cases reach extract_thread_sheets() identically.

    This never touches raw_bytes/raw_name on the AgentContext — those stay the
    true original upload, unwrapped, because they are also what gets stored as
    the retry copy and what the per-sheet fallback pipeline reads directly."""
    from app.services.extraction.file_processor import detect_file_type

    ftype = detect_file_type(filename, data)
    eml_bytes = data if ftype == "eml" else _wrap_as_single_attachment_eml(filename, data)
    return [(filename, eml_bytes)]


async def analyse_upload(db: AsyncSession, *, filename: str, data: bytes) -> dict:
    """Analysis WITHOUT staging — used by the chat-store preview AND by Retry
    (ingestion.retry_pipeline_file re-reads the stored original and calls this
    again). Same two-pass reader as Extract Email; falls back to the per-sheet
    pipeline only when no vision model is configured. Returns
    {sheets, groups, approval, run_meta}."""
    from app.services.extract_email.thread_extract import thread_call_available
    from app.services.orchestrator import (
        AgentContext, Orchestrator, build_pipeline, build_thread_pipeline,
    )

    use_thread = thread_call_available()
    ctx = AgentContext(
        db=db, source_kind="upload", source_id=f"preview:{filename}",
        source=SourceCtx(subject=filename), raw_bytes=data, raw_name=filename,
        thread_messages=as_thread_messages(filename, data) if use_thread else [],
    )
    pipeline = build_thread_pipeline(stage=False) if use_thread else build_pipeline(stage=False)
    await Orchestrator(pipeline).run(ctx)
    return {
        "sheets": ctx.sheets, "groups": ctx.groups,
        "approval": ctx.approval or {"detected": False, "detail": "No readable sheets."},
        "run_meta": ctx.run_meta or {"method": "none"},
    }


async def extract_upload(
    db: AsyncSession, *, filename: str, content_type: str, data: bytes,
    source_id: str | None = None,
) -> dict:
    """Upload page / chat store: the SAME two-pass reader Extract Email uses —
    understand the whole submission, then extract only the sheets it
    confirms — falling back to the per-sheet engine pipeline when no vision
    model is configured (identical fallback rule to extract_full_email).
    Returns the same shape as extract_full_email."""
    import uuid

    from app.services.extract_email.thread_extract import thread_call_available
    from app.services.extract_email.types import SourceCtx
    from app.services.orchestrator import (
        AgentContext, Orchestrator, build_pipeline, build_thread_pipeline,
    )

    use_thread = thread_call_available()
    ctx = AgentContext(
        db=db, source_kind="upload",
        source_id=source_id or f"upload:{uuid.uuid4().hex[:12]}",
        source=SourceCtx(subject=filename), raw_bytes=data, raw_name=filename,
        content_type=content_type,
        thread_messages=as_thread_messages(filename, data) if use_thread else [],
    )
    pipeline = build_thread_pipeline() if use_thread else build_pipeline()
    await Orchestrator(pipeline).run(ctx)

    approval = ctx.approval or {"detected": False, "detail": "No approval check ran."}
    if not ctx.groups:
        kinds = ", ".join(f"{s['name']} ({s['kind']})" for s in ctx.sheets) or "nothing readable"
        return build_result([], [], ctx.sheets, approval,
                            f"Nothing to stage — no timesheet or certificate found ({kinds}).")
    message = staged_message(ctx.groups, approval)
    if ctx.notes:
        message = f"{message} " + " ".join(ctx.notes[:3])
    return build_result(ctx.staged, ctx.groups, ctx.sheets, approval, message)
