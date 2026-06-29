"""
EmailMessage — a lightweight mirror of an inbox message plus our workflow state.

The raw inbox lives in the email provider (mock today, Microsoft Graph later).
We upsert a row here the first time we see a message so we can persist:
  - the manager Accept / Reject decision
  - whether it has been ingested into the extraction pipeline
  - archive state
Attachment bytes are NOT stored here; they are fetched on demand from the
provider via `provider_message_id` + attachment id.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class EmailStatus:
    NEW = "new"            # seen in inbox, no decision yet
    ARCHIVED = "archived"  # manager said "not accepted"
    INGESTED = "ingested"  # manager accepted -> went through pipeline


class EmailMessage(Base):
    __tablename__ = "email_messages"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)

    # Stable id from the provider (Graph messageId, or mock id).
    provider_message_id: Mapped[str] = mapped_column(String, index=True, unique=True)

    sender_name: Mapped[str | None] = mapped_column(String, nullable=True)
    sender_email: Mapped[str | None] = mapped_column(String, nullable=True)
    subject: Mapped[str | None] = mapped_column(String, nullable=True)
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    body_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    # [{ "attachment_id", "filename", "content_type", "size", "kind" }]
    attachments: Mapped[list] = mapped_column(JSON, default=list)

    has_approval_screenshot: Mapped[bool] = mapped_column(Boolean, default=False)

    # Inbox AI check (gpt-4.1-nano): attachment triage + recommended employee.
    ai_check: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    ai_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    status: Mapped[str] = mapped_column(String, default=EmailStatus.NEW, index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
