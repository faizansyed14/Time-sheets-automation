"""Upload and chat extraction entry points."""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.extract_email.collector import collect_units, unit_from_bytes
from app.services.extract_email.results import build_result, staged_message
from app.services.extract_email.types import SheetUnit, SourceCtx

def units_from_upload(filename: str, data: bytes) -> tuple[SourceCtx, list[SheetUnit]]:
    """Uploaded bytes → (context, units). An uploaded .eml unpacks exactly like
    Extract Email (attachments + forwarded emails + body); anything else is one
    sheet."""
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


async def analyse_upload(db: AsyncSession, *, filename: str, data: bytes) -> dict:
    """Analysis WITHOUT staging (chat preview) — the SAME agent pipeline as
    every other entry point, stopped before the Decision Agent so nothing is
    staged or filed. Returns {sheets, groups, approval, run_meta}."""
    from app.services.orchestrator import AgentContext, Orchestrator, build_pipeline

    ctx = AgentContext(
        db=db, source_kind="upload", source_id=f"preview:{filename}",
        source=SourceCtx(subject=filename), raw_bytes=data, raw_name=filename,
    )
    await Orchestrator(build_pipeline(stage=False)).run(ctx)
    return {
        "sheets": ctx.sheets, "groups": ctx.groups,
        "approval": ctx.approval or {"detected": False, "detail": "No readable sheets."},
        "run_meta": ctx.run_meta or {"method": "none"},
    }


async def extract_upload(
    db: AsyncSession, *, filename: str, content_type: str, data: bytes,
    source_id: str | None = None,
) -> dict:
    """Upload page / chat store: the SAME agent pipeline as Extract Email for an
    uploaded file — unpack, route, read, approval, employee, period-merge,
    duplicate check, validation, decision. Returns the same shape as
    extract_full_email."""
    import uuid

    from app.services.extract_email.types import SourceCtx
    from app.services.orchestrator import AgentContext, Orchestrator, build_pipeline

    ctx = AgentContext(
        db=db, source_kind="upload",
        source_id=source_id or f"upload:{uuid.uuid4().hex[:12]}",
        source=SourceCtx(subject=filename), raw_bytes=data, raw_name=filename,
        content_type=content_type,
    )
    await Orchestrator(build_pipeline()).run(ctx)

    approval = ctx.approval or {"detected": False, "detail": "No approval check ran."}
    if not ctx.groups:
        kinds = ", ".join(f"{s['name']} ({s['kind']})" for s in ctx.sheets) or "nothing readable"
        return build_result([], [], ctx.sheets, approval,
                            f"Nothing to stage — no timesheet or certificate found ({kinds}).")
    message = staged_message(ctx.groups, approval)
    if ctx.notes:
        message = f"{message} " + " ".join(ctx.notes[:3])
    return build_result(ctx.staged, ctx.groups, ctx.sheets, approval, message)
