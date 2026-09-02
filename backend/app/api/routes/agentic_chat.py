"""Generic AI chat routes — a plain, general-purpose assistant (no database
access, no tools) that can also read an attached file: PDF/DOCX/XLSX/TXT are
read as plain text (see chat_files.py), an image is sent as an image block.
Same shape as ChatGPT/Claude's own file-attach chat.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_admin
from app.core.database import get_db, SessionLocal
from app.models.auth import Role, User
from app.models.chat_access import CHAT_ACCESS_SINGLETON_ID, ChatAccessConfig
from app.schemas import ChatSuggestions
from app.services.agents import chat_agent, chat_files
from app.services.llm import provider as llm_provider

router = APIRouter(prefix="/agentic-chat", tags=["agentic-chat"])


async def _get_or_create_access(db: AsyncSession) -> ChatAccessConfig:
    row = await db.get(ChatAccessConfig, CHAT_ACCESS_SINGLETON_ID)
    if row is None:
        row = ChatAccessConfig(id=CHAT_ACCESS_SINGLETON_ID)
        db.add(row)
        await db.commit()
        await db.refresh(row)
    return row


def _access_out(row: ChatAccessConfig) -> dict:
    return {
        "enabled_for_others": row.enabled_for_others,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "updated_by": row.updated_by,
    }


@router.get("/access")
async def get_chat_access(db: AsyncSession = Depends(get_db)):
    """Whether "others" (non-admin) can see and use Ask AI — admin always
    can regardless of this flag. Readable by any role that can reach this
    router (the frontend needs it to decide whether to show/lock the page);
    the router's own require_full_access dependency already keeps
    vault_matcher out, and this is a safe GET so viewer isn't blocked either
    — though viewer can't actually chat regardless, since /stream is a POST
    and viewer isn't in Role.WRITERS (see api/deps.py's require_write)."""
    return _access_out(await _get_or_create_access(db))


class ChatAccessIn(BaseModel):
    enabled_for_others: bool


@router.put("/access")
async def update_chat_access(
    body: ChatAccessIn, user: User = Depends(require_admin), db: AsyncSession = Depends(get_db),
):
    row = await _get_or_create_access(db)
    row.enabled_for_others = body.enabled_for_others
    row.updated_by = user.username
    await db.commit()
    await db.refresh(row)
    return _access_out(row)

# A real DoS guard, not a document-size guess: read at most this many bytes
# before giving up, so an oversized upload is rejected cleanly instead of
# being buffered fully into memory first.
MAX_UPLOAD_BYTES = 20 * 1024 * 1024   # 20MB — plenty for a chat attachment


async def _read_capped(file: UploadFile, cap: int) -> bytes | None:
    """Read at most `cap` bytes; None means the file exceeded it — the
    caller rejects it outright rather than ever holding an unbounded upload
    in memory."""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > cap:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


@router.get("/suggestions", response_model=ChatSuggestions)
async def suggestions(db: AsyncSession = Depends(get_db)):
    """Whether an AI provider is configured, and which model — shown in the
    chat header."""
    cfg = await llm_provider.active_config(db, kind="agent")
    return ChatSuggestions(enabled=cfg["has_key"], model=cfg["model"] if cfg["has_key"] else None)


@router.post("/stream")
async def chat_stream(
    messages: str = Form(...), file: UploadFile | None = File(None),
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    """Streaming turn (Server-Sent Events): {"type":"token","text":...} as the
    answer is produced, then {"type":"done","error":...}. `messages` is the
    conversation history as a JSON-encoded string (multipart can't carry a
    JSON body directly); `file`, if given, is read once here and folded into
    the current turn's message as text or an image — never sent to a vision
    model or the app's own extraction pipeline, just read directly.

    The router's own require_full_access (main.py) already keeps viewer and
    vault_matcher out — but that's a broad role check, not the "others"
    access toggle from AI Settings. Enforce that HERE too, not just in the
    frontend's lock screen: a "user"-role token hitting this directly (curl,
    no UI at all) must not bypass an admin turning "others" off."""
    if user.role != Role.ADMIN:
        access = await _get_or_create_access(db)
        if not access.enabled_for_others:
            raise HTTPException(403, "Ask AI isn't open to your account. An admin can turn this on under AI Settings.")

    try:
        history = json.loads(messages)
    except Exception:
        history = []

    attachment = None
    if file is not None:
        data = await _read_capped(file, MAX_UPLOAD_BYTES)
        if data is None:
            attachment = {"kind": "error", "message": (
                f"That file is too large — attachments are capped at "
                f"{MAX_UPLOAD_BYTES // (1024 * 1024)}MB.")}
        elif data:
            attachment = chat_files.extract_attachment(file.filename or "attachment", file.content_type or "", data)

    async def _gen():
        # A fresh session bound to the stream (the request-scoped one closes
        # when the handler returns, before the generator finishes).
        async with SessionLocal() as db:
            try:
                async for ev in chat_agent.run_chat_stream(db, history, attachment):
                    yield f"data: {json.dumps(ev, default=str)}\n\n"
            except Exception as e:  # never leave the stream hanging
                yield f"data: {json.dumps({'type': 'done', 'error': str(e)[:200]})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(_gen(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",   # disable nginx proxy buffering for SSE
    })
