"""
Upload route — the SAME pipeline as Extract Email, for uploaded files:
every sheet analysed (an uploaded .eml unpacks fully — attachments, forwarded
emails, body grids), grouped per employee+month, staged NEEDS_REVIEW →
Compare & Fix → Accept files the record. Nothing files without review.

Manual entry (no LLM, reviewer-entered data) still files immediately via
ingest_manual_entry — it IS the human decision.
"""
from __future__ import annotations

import json as _json

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import datacache
from app.core.database import get_db
from app.schemas import PipelineFileOut, UploadResult
from app.services.agents.full_email_extract import extract_upload
from app.services.pipeline.ingestion import ingest_manual_entry

# Leave buckets a manual entry may carry (matches the extraction buckets).
_MANUAL_BUCKETS = ("annual", "remote", "sick", "maternity", "unpaid", "absent", "public_holiday")

router = APIRouter(prefix="/upload", tags=["upload"])


@router.post("/stream")
async def upload_timesheets_streamed(files: list[UploadFile] = File(...)):
    """Same as POST /upload, but streams live progress (SSE) — one frame per
    pipeline stage/LLM call/auto-accept decision — then a final `done` frame
    with the staged items."""
    from fastapi.responses import StreamingResponse

    from app.api.routes.pipeline import _out as _pipeline_out
    from app.core.database import SessionLocal
    from app.services.extract_email.streaming import sse_events

    batch: list[tuple[str, str, bytes]] = []
    for f in files or []:
        data = await f.read()
        if data:
            batch.append((f.filename or "upload",
                          f.content_type or "application/octet-stream", data))
    if not batch:
        raise HTTPException(400, "No files uploaded")

    async def run() -> dict:
        async with SessionLocal() as db:
            staged = []
            for filename, content_type, data in batch:
                res = await extract_upload(db, filename=filename,
                                           content_type=content_type, data=data)
                staged.extend(res["staged"])
            await datacache.bust_pipeline()
            return {"staged": [_pipeline_out(t).model_dump(mode="json") for t in staged]}

    return StreamingResponse(sse_events(run), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("", response_model=list[PipelineFileOut])
async def upload_timesheets(
    files: list[UploadFile] = File(...),
    db: AsyncSession = Depends(get_db),
):
    if not files:
        raise HTTPException(400, "No files uploaded")
    batch: list[tuple[str, str, bytes]] = []
    for f in files:
        data = await f.read()
        if data:
            batch.append((
                f.filename or "upload",
                f.content_type or "application/octet-stream",
                data,
            ))
    if not batch:
        raise HTTPException(400, "All uploaded files were empty.")
    from app.api.routes.pipeline import _out as _pipeline_out

    staged = []
    for filename, content_type, data in batch:
        res = await extract_upload(
            db, filename=filename, content_type=content_type, data=data)
        staged.extend(res["staged"])
    await datacache.bust_pipeline()
    if not staged:
        raise HTTPException(
            422, "Nothing to review — no timesheet or certificate was found in "
                 "the uploaded file(s).")
    return [_pipeline_out(t) for t in staged]


@router.post("/manual", response_model=UploadResult)
async def upload_manual(
    employee_pk: str = Form(...),
    month: int = Form(...),
    year: int = Form(...),
    buckets: str = Form("{}"),          # JSON: {"annual":["2026-03-01"], ...}
    note: str | None = Form(default=None),
    files: list[UploadFile] = File(default=[]),
    db: AsyncSession = Depends(get_db),
):
    """Create a record from manually entered leave data (no LLM), optionally with
    attached files — same vault filing + pipeline tracker as upload/email."""
    try:
        parsed = _json.loads(buckets or "{}")
        if not isinstance(parsed, dict):
            raise ValueError
    except Exception:
        raise HTTPException(400, "`buckets` must be a JSON object of bucket -> date list.")
    bucket_data = {b: [str(d).strip() for d in (parsed.get(b) or []) if str(d).strip()] for b in _MANUAL_BUCKETS}

    attachments: list[tuple[str, str, bytes]] = []
    for f in files or []:
        data = await f.read()
        if data:
            attachments.append((f.filename or "attachment",
                                f.content_type or "application/octet-stream", data))
    try:
        rec, tracker = await ingest_manual_entry(
            db, employee_pk=employee_pk, month=month, year=year,
            buckets=bucket_data, attachments=attachments, note=note)
    except ValueError as e:
        raise HTTPException(400, str(e))
    await datacache.bust_pipeline()
    return UploadResult(
        pipeline_id=tracker.id, filename=tracker.filename, status=tracker.status,
        failure_code=tracker.failure_code, failure_detail=tracker.failure_detail,
        record_id=rec.id, employee_name=rec.employee_name, employee_id=rec.employee_id,
        month=rec.month, year=rec.year, validation_status=rec.validation_status,
        llm_summary=rec.llm_summary, match_note=rec.match_note,
    )
