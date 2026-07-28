"""Upload and chat extraction entry points.

Upload runs the SAME two-pass reader as Extract Email (see thread_extract.py):
one call to understand the whole submission, one to extract the confirmed
sheets. There is no fallback — a vision model is mandatory."""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.extract_email.results import build_result, staged_message
from app.services.extract_email.types import SourceCtx


def _wrap_as_single_attachment_eml(filename: str, data: bytes) -> bytes:
    """Wrap a bare uploaded file — a PDF/XLSX/DOCX/CSV/TXT/image with no email
    envelope — as a minimal one-attachment message, so it reaches the
    two-pass reader (thread_extract.collect_thread parses real RFC822 bytes)
    the same way a real .eml/.msg upload does. No body text — the file
    itself is the submission."""
    from email.message import EmailMessage as MimeMessage

    from app.services.extraction.file_processor import content_type_for

    # Default policy (not compat32) — set_content/add_attachment need its
    # content_manager. thread_extract.py parses with compat32 afterwards,
    # which reads either policy's wire format identically.
    msg = MimeMessage()
    msg["Subject"] = filename
    msg.set_content("")
    ctype = content_type_for(filename, data, fallback="application/octet-stream")
    maintype, _, subtype = ctype.partition("/")
    msg.add_attachment(data, maintype=maintype or "application",
                       subtype=subtype or "octet-stream", filename=filename)
    return msg.as_bytes()


def as_thread_messages(filename: str, data: bytes) -> list[tuple[str, bytes]]:
    """One uploaded file → one "thread message", for the same two-pass reader
    Extract Email uses. A real .eml/.msg upload already carries everything the
    reader needs (body, real attachments, any forwarded emails); any other
    file type (pdf, docx, xlsx, csv, txt, image) is wrapped first so every
    case reaches extract_thread_sheets() identically.

    This never touches raw_bytes/raw_name on the AgentContext — those stay the
    true original upload, unwrapped, because they are also what gets stored as
    the retry copy."""
    name = (filename or "").lower()
    if name.endswith(".eml"):
        eml_bytes = data
    elif name.endswith(".msg"):
        from app.services.extract_email.thread_extract import msg_to_eml_bytes
        eml_bytes = msg_to_eml_bytes(data) or _wrap_as_single_attachment_eml(filename, data)
    else:
        eml_bytes = _wrap_as_single_attachment_eml(filename, data)
    return [(filename, eml_bytes)]


async def analyse_upload(db: AsyncSession, *, filename: str, data: bytes) -> dict:
    """Analysis WITHOUT staging — used by the chat-store preview AND by Retry
    (ingestion.retry_pipeline_file re-reads the stored original and calls this
    again). Returns {sheets, groups, approval, run_meta}."""
    from app.services.extract_email.thread_extract import require_vision_configured
    from app.services.orchestrator import AgentContext, Orchestrator, build_thread_pipeline

    require_vision_configured()
    ctx = AgentContext(
        db=db, source_kind="upload", source_id=f"preview:{filename}",
        source=SourceCtx(subject=filename), raw_bytes=data, raw_name=filename,
        thread_messages=as_thread_messages(filename, data),
    )
    await Orchestrator(build_thread_pipeline(stage=False)).run(ctx)
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
    confirms. Returns the same shape as extract_full_email."""
    import uuid

    from app.services.extract_email.thread_extract import require_vision_configured
    from app.services.orchestrator import AgentContext, Orchestrator, build_thread_pipeline

    require_vision_configured()
    ctx = AgentContext(
        db=db, source_kind="upload",
        source_id=source_id or f"upload:{uuid.uuid4().hex[:12]}",
        source=SourceCtx(subject=filename), raw_bytes=data, raw_name=filename,
        content_type=content_type,
        thread_messages=as_thread_messages(filename, data),
    )
    await Orchestrator(build_thread_pipeline()).run(ctx)

    approval = ctx.approval or {"detected": False, "detail": "No approval check ran."}
    if not ctx.groups:
        kinds = ", ".join(f"{s['name']} ({s['kind']})" for s in ctx.sheets) or "nothing readable"
        return build_result([], [], ctx.sheets, approval,
                            f"Nothing to stage — no timesheet or certificate found ({kinds}).")
    message = staged_message(ctx.groups, approval)
    if ctx.notes:
        message = f"{message} " + " ".join(ctx.notes[:3])
    return build_result(ctx.staged, ctx.groups, ctx.sheets, approval, message)
