"""Agentic chat routes — a timesheet-scoped assistant (text → safe DB actions)."""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import datacache
from app.core.http_headers import content_disposition
from app.core.database import get_db, SessionLocal
from app.schemas import ChatRequest, ChatResponse, ChatSuggestions, UploadResult
from app.services.agents import chat_agent, upload_cache
from app.services.agents.extract_service import extract_from_upload
from app.services.agents.full_email_extract import extract_upload
from app.services.llm import provider as llm_provider

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
    """Run one assistant turn (non-streaming). The agent may call read/edit
    tools, ask a clarifying question, or refuse off-topic requests."""
    history = [{"role": m.role, "content": m.content} for m in body.messages]
    result = await chat_agent.run_chat(db, history, extractions=body.extractions)
    return ChatResponse(**result)


@router.post("/stream")
async def chat_stream(body: ChatRequest):
    """Streaming turn (Server-Sent Events). Emits token / tool-activity / card /
    suggestion / done events so the UI shows the answer and the agent's work
    live. Owns its own DB session for the whole stream lifetime."""
    history = [{"role": m.role, "content": m.content} for m in body.messages]
    extractions = body.extractions

    async def _gen():
        # A fresh session bound to the stream (the request-scoped one closes when
        # the handler returns, before the generator finishes).
        async with SessionLocal() as db:
            try:
                async for ev in chat_agent.run_chat_stream(db, history, extractions):
                    yield f"data: {json.dumps(ev, default=str)}\n\n"
            except Exception as e:  # never leave the stream hanging
                yield f"data: {json.dumps({'type': 'done', 'error': str(e)[:200]})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(_gen(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",   # disable nginx proxy buffering for SSE
    })


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
        token = await upload_cache.put(data, filename, content_type)
    except ValueError as e:
        raise HTTPException(413, str(e)) from e

    return {**result, "token": token, "content_type": content_type}


@router.post("/attachments/{token}/store", response_model=UploadResult)
async def store_uploaded_sheet(token: str, db: AsyncSession = Depends(get_db)):
    """Opt-in staging: only called when the user explicitly confirms they want
    this chat-uploaded sheet processed. Runs the SAME pipeline as Extract
    Email / Upload — every sheet analysed, grouped, staged NEEDS_REVIEW for
    Compare & Fix. Nothing files a record until a reviewer Accepts it there.
    The ephemeral chat copy is consumed (popped) so it can't be staged twice."""
    entry = await upload_cache.pop(token)
    if not entry:
        raise HTTPException(404, "Attachment expired or not found. Please re-upload it.")
    res = await extract_upload(
        db, filename=entry.filename, content_type=entry.content_type, data=entry.data)
    await datacache.bust_pipeline()
    staged = res["staged"]
    if not staged:
        raise HTTPException(422, res["message"])
    t = staged[0]
    meta = (t.extraction_meta or {}).get("staged") or {}
    return UploadResult(
        pipeline_id=t.id, filename=t.filename, status=t.status,
        failure_code=t.failure_code,
        failure_detail=res["message"],
        record_id=None,
        employee_name=t.employee_name, employee_id=t.employee_id,
        month=t.month, year=t.year,
        validation_status=meta.get("validation_status"),
        llm_summary=meta.get("summary"),
        match_note="Staged for review — open the Pipeline page (or Compare & Fix) to accept.",
    )


@router.get("/attachments/{token}/eml-preview")
async def preview_uploaded_eml(token: str):
    """Parse a chat-uploaded .eml from the ephemeral store (Outlook-style view)."""
    entry = await upload_cache.get(token)
    if not entry:
        raise HTTPException(404, "Attachment expired or not found.")
    if not entry.filename.lower().endswith(".eml"):
        raise HTTPException(400, "Not an EML file")
    from app.services.extraction.eml_parser import parse_eml
    return parse_eml(entry.data)


@router.get("/attachments/{token}")
async def preview_uploaded_sheet(token: str):
    """Serve a chat-uploaded file from the ephemeral store for preview only."""
    entry = await upload_cache.get(token)
    if not entry:
        raise HTTPException(404, "Attachment expired or not found.")
    disposition = "inline" if entry.content_type.startswith(
        ("image/", "application/pdf", "message/")) else "attachment"
    return Response(
        content=entry.data,
        media_type=entry.content_type,
        headers={"Content-Disposition": content_disposition(disposition, entry.filename)},
    )
