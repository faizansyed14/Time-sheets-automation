"""System notice — a single admin-authored message shown as a popup to every
logged-in user (any role, including viewer/vault_matcher) when enabled.

Singleton row (id="singleton"), same pattern as reminder_config
(models/reminder.py) — deliberately the simplest possible design since this
is ever only one sentence, not a list of announcements.

Acknowledgement: the popup has a single "OK" button. A click is stored right on
this row in `acked_by` ({user_id: ISO time}), so it follows the user across
browsers/devices, the popup never comes back for them, and the admin can see who
has clicked. `acked_by` is emptied whenever the announcement changes (new
message, or switched back on), so a new announcement shows to everyone again.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, String, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

NOTICE_SINGLETON_ID = "singleton"


class SystemNotice(Base):
    __tablename__ = "system_notice"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: NOTICE_SINGLETON_ID)
    message: Mapped[str] = mapped_column(String, nullable=False, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # {user_id: ISO timestamp of their "OK"} for the CURRENT announcement.
    acked_by: Mapped[dict] = mapped_column(
        JSON, nullable=False, default=dict, server_default=text("'{}'"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    updated_by: Mapped[str | None] = mapped_column(String, nullable=True)
