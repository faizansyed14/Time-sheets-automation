"""Agentic chat routes — a timesheet-scoped assistant (text → safe DB actions)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.schemas import ChatRequest, ChatResponse, ChatSuggestions
from app.services.agents import chat_agent, upload_cache
from app.services.agents.extract_service import extract_from_upload
from app.services.llm import provider as llm_provider

router = APIRouter(prefix="/agentic-chat", tags=["agentic-chat"])


@router.get("/suggestions", response_model=ChatSuggestions)
async def suggestions(db: AsyncSession = Depends(get_db)):
    """Starter questions + the prompt book shown when the chat opens."""
    cfg = await llm_provider.active_config(db, kind="extraction")
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
