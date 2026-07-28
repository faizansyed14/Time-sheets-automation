"""ExtractionDebugRun — a temporary, purgeable full trace of one Extract
Email/Upload run: every pass-1/pass-2 call's prompt text + response JSON,
every dropped/noise item, and the unabridged final sheets/triage JSON.

Everything here is ALSO visible live via progress.py's emit() during a
streamed run, but that is in-memory/per-request only — gone the moment the
request ends. This is the same information, persisted, for going back and
inspecting exactly what a run sent/received/dropped while testing prompt or
extraction quality. Meant to be bulk-deleted once done (see
DELETE /admin/debug/runs) — not a permanent audit log.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class ExtractionDebugRun(Base):
    __tablename__ = "extraction_debug_runs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    source_kind: Mapped[str | None] = mapped_column(String, nullable=True)   # "email" | "upload"
    source_id: Mapped[str | None] = mapped_column(String, nullable=True)
    thread_key: Mapped[str | None] = mapped_column(String, nullable=True)
    subject: Mapped[str | None] = mapped_column(String, nullable=True)
    pipeline_file_id: Mapped[str | None] = mapped_column(String, nullable=True)
    model: Mapped[str | None] = mapped_column(String, nullable=True)
    calls: Mapped[int] = mapped_column(Integer, default=0)
    reused_sheets: Mapped[int] = mapped_column(Integer, default=0)

    # [{"batch_index", "label", "system_prompt", "user_text", "image_refs":
    #   [path], "response_json"}]
    pass1_calls: Mapped[list] = mapped_column(JSON, default=list)
    pass2_calls: Mapped[list] = mapped_column(JSON, default=list)
    # [{"name", "reason", "filter", "size", "mime", "msg_index", "image_path"}]
    dropped_items: Mapped[list] = mapped_column(JSON, default=list)
    triage: Mapped[list] = mapped_column(JSON, default=list)
    sheets: Mapped[list] = mapped_column(JSON, default=list)
    errors: Mapped[list] = mapped_column(JSON, default=list)
