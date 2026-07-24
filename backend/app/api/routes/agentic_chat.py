"""Agentic chat routes — a timesheet-scoped assistant (text → safe DB actions).

Chat is a READ/UPDATE agent only: it answers questions about employees,
records and the pipeline, and applies safe edits through its tools. It does
NOT accept file uploads — the three ingestion paths are Extract Email,
Upload and Manual Entry.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db, SessionLocal
from app.schemas import ChatRequest, ChatResponse, ChatSuggestions
from app.services.agents import chat_agent
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
    result = await chat_agent.run_chat(db, history)
    return ChatResponse(**result)


@router.post("/stream")
async def chat_stream(body: ChatRequest):
    """Streaming turn (Server-Sent Events). Emits token / tool-activity / card /
    suggestion / done events so the UI shows the answer and the agent's work
    live. Owns its own DB session for the whole stream lifetime."""
    history = [{"role": m.role, "content": m.content} for m in body.messages]

    async def _gen():
        # A fresh session bound to the stream (the request-scoped one closes when
        # the handler returns, before the generator finishes).
        async with SessionLocal() as db:
            try:
                async for ev in chat_agent.run_chat_stream(db, history):
                    yield f"data: {json.dumps(ev, default=str)}\n\n"
            except Exception as e:  # never leave the stream hanging
                yield f"data: {json.dumps({'type': 'done', 'error': str(e)[:200]})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(_gen(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",   # disable nginx proxy buffering for SSE
    })
