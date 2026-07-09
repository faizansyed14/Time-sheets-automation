"""Agentic chat routes — a timesheet-scoped assistant (text → safe DB actions)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import datacache
from app.core.database import get_db
from app.schemas import ChatRequest, ChatResponse, ChatSuggestions, UploadResult
from app.services.agents import chat_agent, upload_cache
from app.services.agents.extract_service import extract_from_upload
from app.services.llm import provider as llm_provider
from app.services.pipeline.ingestion import ingest_upload

router = APIRouter(prefix="/agentic-chat", tags=["agentic-chat"])


@router.get("/suggestions", response_model=ChatSuggestions)
async def suggestions(db: AsyncSession = Depends(get_db)):
    """Starter questions + the prompt book shown when the chat opens."""
    cfg = await llm_provider.active_config(db, kind="agent")
    return ChatSuggestions(
        suggestions=chat_agent.SUGGESTIONS,
        prompt_book=chat_agent.PROMPT_BOOK,
        enabled=cfg["has_key"],
        model=cfg["model"] if cfg["has_key"] else None,
    )


@router.post("", response_model=ChatResponse)
async def chat(body: ChatRequest, db: AsyncSession = Depends(get_db)):
    """Run one assistant turn. The agent may call read/edit tools, ask a
    clarifying question, or refuse off-topic requests."""
    history = [{"role": m.role, "content": m.content} for m in body.messages]
    result = await chat_agent.run_chat(db, history, extractions=body.extractions)
    return ChatResponse(**result)


# Accepted upload types for chat extraction.
_ALLOWED_EXTS = (".pdf", ".docx", ".xlsx", ".eml")


@router.post("/extract")
async def extract_uploaded_sheet(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """Extract leaves from a sheet uploaded into the chat — grounded by the same
    extraction pipeline the rest of the app uses. The file is held in memory for
    preview only (token), never saved to storage or the database."""
    filename = file.filename or "upload"
    if not filename.lower().endswith(_ALLOWED_EXTS):
        raise HTTPException(400, "Accepted file types: PDF, DOCX, XLSX, EML.")
    data = await file.read()
    content_type = file.content_type or "application/octet-stream"

    result = await extract_from_upload(
        db, filename=filename, content_type=content_type, data=data)

    # Keep the bytes only long enough to preview them in the chat (ephemeral).
    try:
        token = upload_cache.put(data, filename, content_type)
    except ValueError as e:
        raise HTTPException(413, str(e)) from e

    return {**result, "token": token, "content_type": content_type}


@router.post("/attachments/{token}/store", response_model=UploadResult)
async def store_uploaded_sheet(token: str, db: AsyncSession = Depends(get_db)):
    """Opt-in persistence: only called when the user explicitly confirms they
    want this chat-uploaded sheet filed. Runs the file through the exact same
    pipeline as the Upload page (ingest_upload) — extract, match, validate,
    file — and creates a PipelineFile + TimesheetRecord like any other upload.
    The ephemeral chat copy is consumed (popped) so it can't be filed twice."""
    entry = upload_cache.pop(token)
    if not entry:
        raise HTTPException(404, "Attachment expired or not found. Please re-upload it.")
    rec, tracker = await ingest_upload(
        db, filename=entry.filename, content_type=entry.content_type, data=entry.data)
    await datacache.bust_pipeline()
    return UploadResult(
        pipeline_id=tracker.id, filename=tracker.filename, status=tracker.status,
        failure_code=tracker.failure_code, failure_detail=tracker.failure_detail,
        record_id=rec.id if rec else None,
        employee_name=rec.employee_name if rec else tracker.employee_name,
        employee_id=rec.employee_id if rec else tracker.employee_id,
        month=rec.month if rec else tracker.month,
        year=rec.year if rec else tracker.year,
        validation_status=rec.validation_status if rec else None,
        llm_summary=rec.llm_summary if rec else None,
        match_note=rec.match_note if rec else None,
    )


@router.get("/attachments/{token}/eml-preview")
async def preview_uploaded_eml(token: str):
    """Parse a chat-uploaded .eml from the ephemeral store (Outlook-style view)."""
    entry = upload_cache.get(token)
    if not entry:
        raise HTTPException(404, "Attachment expired or not found.")
    if not entry.filename.lower().endswith(".eml"):
        raise HTTPException(400, "Not an EML file")
    from app.services.extraction.eml_parser import parse_eml
    return parse_eml(entry.data)


@router.get("/attachments/{token}")
async def preview_uploaded_sheet(token: str):
    """Serve a chat-uploaded file from the ephemeral store for preview only."""
    entry = upload_cache.get(token)
    if not entry:
        raise HTTPException(404, "Attachment expired or not found.")
    disposition = "inline" if entry.content_type.startswith(
        ("image/", "application/pdf", "message/")) else "attachment"
    return Response(
        content=entry.data,
        media_type=entry.content_type,
        headers={"Content-Disposition": f'{disposition}; filename="{entry.filename}"'},
    )
