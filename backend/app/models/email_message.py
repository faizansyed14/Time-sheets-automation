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

    # Groups this message with its replies/forwards (Graph conversationId).
    # Null on rows synced before this column existed, or when the provider
    # can't supply one — the message then renders as its own singleton thread.
    conversation_id: Mapped[str | None] = mapped_column(String, index=True, nullable=True)

    sender_name: Mapped[str | None] = mapped_column(String, nullable=True)
    sender_email: Mapped[str | None] = mapped_column(String, nullable=True)
    # [{ "name": str | None, "email": str }]
    to_recipients: Mapped[list] = mapped_column(JSON, default=list)
    cc_recipients: Mapped[list] = mapped_column(JSON, default=list)
    subject: Mapped[str | None] = mapped_column(String, nullable=True)
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    body_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    # [{ "attachment_id", "filename", "content_type", "size", "kind" }]
    attachments: Mapped[list] = mapped_column(JSON, default=list)

    has_approval_screenshot: Mapped[bool] = mapped_column(Boolean, default=False)

    # HTML body — populated when the email provider returns HTML (e.g. Graph).
    # Used for rich rendering in the UI; body_text is the plain-text fallback
    # and is used for search and the agent pipeline (smaller, no markup noise).
    body_html: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[str] = mapped_column(String, default=EmailStatus.NEW, index=True)

    # Set when Extract Email ran and found NOTHING to stage (no timesheet or
    # certificate in any sheet) — lets the UI show a persistent "No sheets
    # found" badge/filter so the same email isn't reprocessed by hand every
    # time. Cleared if a later run does find something to stage.
    no_sheets_found_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    no_sheets_note: Mapped[str | None] = mapped_column(String, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
