"""
Upload route — manually upload a timesheet that runs the SAME pipeline as the
email "Accept" action (extract -> validate -> match -> file -> record).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.schemas import UploadResult
from app.services.extraction.file_processor import detect_file_type
from app.services.ingestion import ingest_upload

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
        ftype = detect_file_type(f.filename or "upload", data)
        if ftype not in {"pdf", "docx", "xlsx", "image", "eml"}:
            raise HTTPException(400, f"Unsupported file type: {f.filename}")
        rec = await ingest_upload(
            db, filename=f.filename or "upload",
            content_type=f.content_type or "application/octet-stream", data=data,
        )
        results.append(UploadResult(
            record_id=rec.id, employee_name=rec.employee_name, employee_id=rec.employee_id,
            month=rec.month, year=rec.year, validation_status=rec.validation_status,
            llm_summary=rec.llm_summary, match_note=rec.match_note,
        ))
    return results
