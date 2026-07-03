"""
Pipeline tracker routes — full visibility of every file that entered the
extraction pipeline: where it is, where it failed and why, plus Resolve
(human sign-off) and Retry (re-run after fixing the cause).
"""
from __future__ import annotations

from datetime import datetime, timezone

import json as _json

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import datacache
from app.core.database import get_db
from app.models.pipeline_file import FailureCode, PipelineFile, PipelineStage, PipelineStatus
from app.schemas import Page, PipelineFileOut, PipelineResolveAssignIn, PipelineResolveIn, PipelineStats
from app.services.pipeline.ingestion import can_resolve_assign, resolve_pipeline_with_employee, retry_pipeline_file

router = APIRouter(prefix="/pipeline", tags=["pipeline"])

# Human-readable labels the UI can show next to each failure code.
FAILURE_LABELS: dict[str, str] = {
    FailureCode.PROTECTED_PDF: "Protected PDF",
    FailureCode.UNSUPPORTED_TYPE: "Unsupported file type",
    FailureCode.EMPTY_FILE: "Empty file",
    FailureCode.LLM_FAILED: "LLM extraction failed",
    FailureCode.EXTRACTION_UNREADABLE: "Sheet unreadable",
    FailureCode.NAME_NOT_FOUND: "Name not found",
    FailureCode.MONTH_NOT_FOUND: "Month not found",
    FailureCode.EMPLOYEE_NOT_MATCHED: "Employee not in matcher",
    FailureCode.AMBIGUOUS_ID: "Ambiguous employee ID",
    FailureCode.ID_NAME_MISMATCH: "ID / name mismatch",
    FailureCode.VALIDATION_MISMATCH: "Validation mismatch",
    FailureCode.STORAGE_ERROR: "Storage error",
    FailureCode.DUPLICATE_FILE: "Duplicate file",
    FailureCode.PENDING_REVIEW: "Awaiting review",
    FailureCode.UNKNOWN: "Unknown error",
}


def _out(t: PipelineFile) -> PipelineFileOut:
    return PipelineFileOut(
        id=t.id, filename=t.filename, content_type=t.content_type, size_bytes=t.size_bytes,
        source_kind=t.source_kind, source_id=t.source_id, attachment_id=t.attachment_id,
        status=t.status, stage=t.stage, failure_code=t.failure_code,
        failure_label=FAILURE_LABELS.get(t.failure_code or "", None),
        failure_detail=t.failure_detail, events=t.events or [],
        employee_id=t.employee_id, employee_name=t.employee_name,
        month=t.month, year=t.year, record_id=t.record_id,
        extraction_model=t.extraction_model, extraction_method=t.extraction_method,
        used_ocr=bool(t.used_ocr), extraction_meta=t.extraction_meta,
        can_retry=bool(t.raw_path or (t.source_kind == "email" and t.attachment_id)),
        can_resolve_assign=can_resolve_assign(t),
        resolved_at=t.resolved_at, resolution_note=t.resolution_note,
        created_at=t.created_at, updated_at=t.updated_at,
    )


@router.get("", response_model=Page[PipelineFileOut])
async def list_pipeline_files(
    status: str | None = Query(default=None, description="processing|success|needs_review|failed|resolved"),
    failure_code: str | None = Query(default=None),
    source_kind: str | None = Query(default=None, description="upload|email"),
    q: str | None = Query(default=None, description="search filename / employee (whole table)"),
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """Paginated pipeline tracker. Filters + search run in SQL across the whole
    table so scrolling/searching never misses rows beyond the current page."""
    base = select(PipelineFile)
    if status:
        base = base.where(PipelineFile.status == status)
    if failure_code:
        base = base.where(PipelineFile.failure_code == failure_code)
    if source_kind:
        base = base.where(PipelineFile.source_kind == source_kind)
    if q and q.strip():
        like = f"%{q.strip().lower()}%"
        base = base.where(or_(
            func.lower(PipelineFile.filename).like(like),
            func.lower(PipelineFile.employee_name).like(like),
            func.lower(PipelineFile.employee_id).like(like),
        ))

    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    rows = (await db.execute(
        base.order_by(PipelineFile.created_at.desc()).limit(limit).offset(offset)
    )).scalars().all()
    return Page(items=[_out(t) for t in rows], total=total, limit=limit, offset=offset,
                has_more=offset + len(rows) < total)


@router.get("/stats", response_model=PipelineStats)
async def pipeline_stats(db: AsyncSession = Depends(get_db)):
    async def _compute() -> dict:
        rows = (await db.execute(select(PipelineFile))).scalars().all()
        by_status: dict[str, int] = {}
        by_failure: dict[str, int] = {}
        for t in rows:
            by_status[t.status] = by_status.get(t.status, 0) + 1
            if t.status in (PipelineStatus.FAILED, PipelineStatus.NEEDS_REVIEW) and t.failure_code:
                by_failure[t.failure_code] = by_failure.get(t.failure_code, 0) + 1
        return {
            "total": len(rows),
            "processing": by_status.get(PipelineStatus.PROCESSING, 0),
            "success": by_status.get(PipelineStatus.SUCCESS, 0),
            "needs_review": by_status.get(PipelineStatus.NEEDS_REVIEW, 0),
            "failed": by_status.get(PipelineStatus.FAILED, 0),
            "resolved": by_status.get(PipelineStatus.RESOLVED, 0),
            "by_failure_code": by_failure,
            "failure_labels": FAILURE_LABELS,
        }

    # Cached (short TTL) — the UI polls this every 15s from several screens.
    data = await datacache.get_or_set(datacache.NS_PIPELINE, "stats", datacache.TTL_STATS, _compute)
    return PipelineStats(**data)


@router.get("/{pipeline_id}", response_model=PipelineFileOut)
async def get_pipeline_file(pipeline_id: str, db: AsyncSession = Depends(get_db)):
    t = (await db.execute(select(PipelineFile).where(PipelineFile.id == pipeline_id))).scalar_one_or_none()
    if not t:
        raise HTTPException(404, "Pipeline file not found")
    return _out(t)


@router.post("/{pipeline_id}/resolve", response_model=PipelineFileOut)
async def resolve_pipeline_file(
    pipeline_id: str, body: PipelineResolveIn, db: AsyncSession = Depends(get_db),
):
    """Human sign-off: mark a failed / needs-review file as resolved."""
    t = (await db.execute(select(PipelineFile).where(PipelineFile.id == pipeline_id))).scalar_one_or_none()
    if not t:
        raise HTTPException(404, "Pipeline file not found")
    if t.status not in (PipelineStatus.FAILED, PipelineStatus.NEEDS_REVIEW):
        raise HTTPException(409, f"Only failed / needs-review files can be resolved (status is '{t.status}').")
    t.status = PipelineStatus.RESOLVED
    t.resolved_at = datetime.now(timezone.utc)
    t.resolution_note = (body.note or "").strip() or "Marked resolved by reviewer."
    t.events = (t.events or []) + [{
        "stage": t.stage, "status": "ok",
        "detail": f"Resolved by reviewer: {t.resolution_note}",
        "at": t.resolved_at.isoformat(),
    }]
    # Resolved => no longer awaiting a retry, so drop the raw copy.
    from app.services.pipeline.ingestion import purge_raw_copy
    purge_raw_copy(t)
    await db.commit()
    await db.refresh(t)
    await datacache.bust_pipeline()
    return _out(t)


@router.post("/{pipeline_id}/resolve-assign", response_model=PipelineFileOut)
async def resolve_pipeline_assign(
    pipeline_id: str, body: PipelineResolveAssignIn, db: AsyncSession = Depends(get_db),
):
    """Pick the correct employee + period, re-run extraction and file the timesheet."""
    t = (await db.execute(select(PipelineFile).where(PipelineFile.id == pipeline_id))).scalar_one_or_none()
    if not t:
        raise HTTPException(404, "Pipeline file not found")
    if not (1 <= body.month <= 12 and body.year >= 2000):
        raise HTTPException(400, "Invalid month or year.")
    try:
        _rec, t = await resolve_pipeline_with_employee(
            db, t, employee_pk=body.employee_pk, month=body.month, year=body.year, note=body.note,
        )
    except ValueError as e:
        raise HTTPException(409, str(e))
    except FileNotFoundError as e:
        raise HTTPException(409, str(e))
    if t.status == PipelineStatus.FAILED:
        raise HTTPException(409, t.failure_detail or "Processing failed after manual assignment.")
    await datacache.bust_pipeline()
    return _out(t)


@router.post("/{pipeline_id}/retry", response_model=PipelineFileOut)
async def retry_pipeline(pipeline_id: str, db: AsyncSession = Depends(get_db)):
    """Re-run the pipeline on the stored copy of the file (e.g. after adding
    the missing employee to the matcher, or fixing the LLM key)."""
    t = (await db.execute(select(PipelineFile).where(PipelineFile.id == pipeline_id))).scalar_one_or_none()
    if not t:
        raise HTTPException(404, "Pipeline file not found")
    try:
        _rec, t = await retry_pipeline_file(db, t)
    except FileNotFoundError as e:
        raise HTTPException(409, str(e))
    await datacache.bust_pipeline()
    return _out(t)


@router.delete("/{pipeline_id}")
async def delete_pipeline_file(pipeline_id: str, db: AsyncSession = Depends(get_db)):
    t = (await db.execute(select(PipelineFile).where(PipelineFile.id == pipeline_id))).scalar_one_or_none()
    if not t:
        raise HTTPException(404, "Pipeline file not found")
    # Remove the retry copy (S3 or local) before dropping the tracker row.
    from app.services.pipeline.ingestion import purge_raw_copy
    purge_raw_copy(t)
    await db.delete(t)
    await db.commit()
    await datacache.bust_pipeline()
    return {"deleted": pipeline_id}


_MANUAL_BUCKETS = ("annual", "remote", "sick", "unpaid", "absent", "public_holiday")


@router.post("/{pipeline_id}/manual-fix", response_model=PipelineFileOut)
async def pipeline_manual_fix(
    pipeline_id: str,
    employee_pk: str = Form(...),
    month: int = Form(...),
    year: int = Form(...),
    buckets: str = Form("{}"),
    note: str | None = Form(default=None),
    approval_status: str | None = Form(default=None),   # "approved" | "not_approved"
    approval_detail: str | None = Form(default=None),
    files: list[UploadFile] = File(default=[]),
    db: AsyncSession = Depends(get_db),
):
    """Resolve a failed/needs-review file by manually entering leave data.

    Identical to /upload/manual but re-uses (and resolves) the existing
    pipeline tracker instead of creating a new one. Raw copy is purged on
    success so the S3 _pipeline-raw object is deleted.
    """
    t = (await db.execute(select(PipelineFile).where(PipelineFile.id == pipeline_id))).scalar_one_or_none()
    if not t:
        raise HTTPException(404, "Pipeline file not found")
    if t.status not in (PipelineStatus.FAILED, PipelineStatus.NEEDS_REVIEW):
        raise HTTPException(409, f"Only failed/needs-review files can be fixed (status: '{t.status}').")
    if not (1 <= month <= 12 and year >= 2000):
        raise HTTPException(400, "Invalid month or year.")

    try:
        parsed = _json.loads(buckets or "{}")
        if not isinstance(parsed, dict):
            raise ValueError
    except Exception:
        raise HTTPException(400, "`buckets` must be a JSON object of bucket -> date list.")
    bucket_data = {
        b: [str(d).strip() for d in (parsed.get(b) or []) if str(d).strip()]
        for b in _MANUAL_BUCKETS
    }

    attachments: list[tuple[str, str, bytes]] = []
    for f in files or []:
        data = await f.read()
        if data:
            attachments.append((f.filename or "attachment",
                                 f.content_type or "application/octet-stream", data))

    from app.services.pipeline.ingestion import ingest_manual_entry, purge_raw_copy, read_raw_copy

    # If the reviewer didn't attach a replacement file, carry the original raw
    # file forward as the record attachment so it is stored in the File Vault.
    if not attachments and t.raw_path:
        raw_bytes = read_raw_copy(t)
        if raw_bytes:
            import mimetypes as _mt
            fn = t.filename or "attachment"
            ct = _mt.guess_type(fn)[0] or "application/octet-stream"
            attachments.append((fn, ct, raw_bytes))
    approval = None
    if approval_status in ("approved", "not_approved"):
        approval = {"approved": approval_status == "approved",
                    "detail": (approval_detail or "").strip()}

    try:
        rec, new_tracker = await ingest_manual_entry(
            db, employee_pk=employee_pk, month=month, year=year,
            buckets=bucket_data, attachments=attachments, note=note,
            approval=approval,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))

    # ingest_manual_entry created its own tracker — delete it; the original t
    # is the authoritative audit row for this file.
    await db.delete(new_tracker)

    # Update original tracker to reflect the manual resolution.
    t.record_id = rec.id
    t.status = PipelineStatus.SUCCESS
    t.failure_code = None
    t.failure_detail = None
    t.resolved_at = datetime.now(timezone.utc)
    t.resolution_note = (note or "").strip() or "Resolved via manual entry."
    t.month = rec.month
    t.year = rec.year
    t.employee_name = rec.employee_name
    t.employee_id = rec.employee_id
    t.events = (t.events or []) + [{
        "stage": "recorded", "status": "ok",
        "detail": f"Manually resolved by reviewer: {t.resolution_note}",
        "at": t.resolved_at.isoformat(),
    }]
    purge_raw_copy(t)
    await db.commit()
    await db.refresh(t)
    await datacache.bust_pipeline()
    return _out(t)


@router.get("/{pipeline_id}/raw-preview")
async def pipeline_raw_preview(pipeline_id: str, db: AsyncSession = Depends(get_db)):
    """Serve the stored raw file for inline preview (PDF / image / EML download)."""
    from fastapi import Response as _Response
    t = (await db.execute(select(PipelineFile).where(PipelineFile.id == pipeline_id))).scalar_one_or_none()
    if not t:
        raise HTTPException(404, "Pipeline file not found")
    from app.services.pipeline.ingestion import read_raw_copy
    data = read_raw_copy(t)
    if not data:
        raise HTTPException(404, "Raw file copy is no longer available")
    ctype = t.content_type or "application/octet-stream"
    fname = t.filename or "file"
    inline_types = ("image/", "application/pdf", "message/", "text/")
    disp = "inline" if any(ctype.startswith(p) for p in inline_types) else "attachment"
    return _Response(content=data, media_type=ctype,
                     headers={"Content-Disposition": f'{disp}; filename="{fname}"'})


@router.get("/{pipeline_id}/raw-render")
async def pipeline_raw_render(
    pipeline_id: str,
    page: int = Query(default=1, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
):
    """Server-side render of the raw DOCX/XLSX/PDF copy to a page image —
    previews that work in every browser; the original stays downloadable."""
    from fastapi import Response as _Response
    from app.services.extraction.file_processor import detect_file_type, to_images
    t = (await db.execute(select(PipelineFile).where(PipelineFile.id == pipeline_id))).scalar_one_or_none()
    if not t:
        raise HTTPException(404, "Pipeline file not found")
    from app.services.pipeline.ingestion import read_raw_copy
    data = read_raw_copy(t)
    if not data:
        raise HTTPException(404, "Raw file copy is no longer available")
    ftype = detect_file_type(t.filename or "", data)
    if ftype not in ("docx", "xlsx", "pdf"):
        raise HTTPException(400, f"No server render for type '{ftype}'")
    imgs = to_images(ftype, data)
    if not imgs:
        raise HTTPException(422, "Could not render this file")
    idx = min(page, len(imgs)) - 1
    return _Response(content=imgs[idx], media_type="image/jpeg",
                     headers={"X-Page-Count": str(len(imgs))})


@router.get("/{pipeline_id}/raw-eml-preview")
async def pipeline_raw_eml_preview(pipeline_id: str, db: AsyncSession = Depends(get_db)):
    """Parse the raw EML file and return structured content as JSON for the viewer."""
    t = (await db.execute(select(PipelineFile).where(PipelineFile.id == pipeline_id))).scalar_one_or_none()
    if not t:
        raise HTTPException(404, "Pipeline file not found")
    if not (t.filename or "").lower().endswith(".eml"):
        raise HTTPException(400, "Not an EML file")
    from app.services.pipeline.ingestion import read_raw_copy
    data = read_raw_copy(t)
    if not data:
        raise HTTPException(404, "Raw file copy is no longer available")
    from app.services.extraction.eml_parser import parse_eml
    return parse_eml(data)


@router.get("/meta/stages")
async def pipeline_stages():
    return {"stages": PipelineStage.ORDER, "failure_labels": FAILURE_LABELS}
