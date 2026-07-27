"""MonthCalendar — admin-configured weekends + public holidays for one
(month, year), fed into Pass 2 of the extraction prompt as ground truth
instead of the model inferring weekday-of-month / holidays itself.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class MonthCalendar(Base):
    __tablename__ = "month_calendars"
    __table_args__ = (UniqueConstraint("month", "year", name="uq_month_calendars_month_year"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    month: Mapped[int] = mapped_column(Integer)
    year: Mapped[int] = mapped_column(Integer)
    # Weekday NAMES ("Friday", "Saturday", ...) — a policy choice, not derivable.
    weekend_weekdays: Mapped[list] = mapped_column(JSON, default=list)
    # [{"date": "2026-06-15", "name": "Eid al-Adha"}, ...] — announced
    # externally, not derivable at all.
    public_holidays: Mapped[list] = mapped_column(JSON, default=list)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
