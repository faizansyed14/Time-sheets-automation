"""Ask AI access — whether non-admin users ("others") can see and use the
generic AI chat. Singleton row (id="singleton"), same pattern as
system_notice/reminder_config. Default False preserves the app's existing
admin-only behavior until an admin explicitly turns it on for everyone else.

Admin always has access regardless of this flag — it only ever restricts
OTHERS. The read-only "viewer" role is excluded either way: the chat's own
POST endpoint is gated by require_full_access (see routes/agentic_chat.py),
which — like every write endpoint — blocks viewer's non-GET requests
structurally, not just in the UI, so there is nothing for this toggle to
unlock for that role.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

CHAT_ACCESS_SINGLETON_ID = "singleton"


class ChatAccessConfig(Base):
    __tablename__ = "chat_access_config"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: CHAT_ACCESS_SINGLETON_ID)
    enabled_for_others: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    updated_by: Mapped[str | None] = mapped_column(String, nullable=True)
