"""
Upload route — manually upload timesheets that run the SAME pipeline as the
email "Accept" action (extract -> validate -> match -> file -> record).

Every file gets a PipelineFile tracker row; a failing file no longer aborts
the batch or returns a 500 — its failure code/detail comes back in the result
and is visible on the Pipeline page (with Resolve / Retry).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import datacache
from app.core.database import get_db
from app.schemas import UploadResult
from app.services.pipeline.ingestion import ingest_upload

router = APIRouter(prefix="/upload", tags=["upload"])


@router.post("", response_model=list[UploadResult])
async def upload_timesheets(
    files: list[UploadFile] = File(...),
    db: AsyncSession = Depends(get_db),
):
    if not files:
        raise HTTPException(400, "No files uploaded")
    results: list[UploadResult] = []
    for f in files:
        data = await f.read()
        rec, tracker = await ingest_upload(
            db, filename=f.filename or "upload",
            content_type=f.content_type or "application/octet-stream", data=data,
        )
        results.append(UploadResult(
            pipeline_id=tracker.id,
            filename=tracker.filename,
            status=tracker.status,
            failure_code=tracker.failure_code,
            failure_detail=tracker.failure_detail,
            record_id=rec.id if rec else None,
            employee_name=rec.employee_name if rec else tracker.employee_name,
            employee_id=rec.employee_id if rec else tracker.employee_id,
            month=rec.month if rec else tracker.month,
            year=rec.year if rec else tracker.year,
            validation_status=rec.validation_status if rec else None,
            llm_summary=rec.llm_summary if rec else None,
            match_note=rec.match_note if rec else None,
        ))
    await datacache.bust_pipeline()
    return results
