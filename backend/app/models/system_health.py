"""
System health monitoring — tracks whether the LLM provider (API key /
credits) and Microsoft Graph (OTP + reminder + inbox mail transport) are
actually working, so an expired key/secret or exhausted credits surfaces as
an admin email instead of silently failing every extraction/OTP/reminder
send from then on.

One row per component ("llm", "graph"), not a log — this only remembers the
CURRENT state (plus when it last changed / was last alerted on) so a
scheduled check can tell "still broken, already told them" apart from
"just broke, send the alert" without re-sending every tick.

Deliberately its own table/model, not reused from reminder_config or
chat_access — this is an operational signal about the SYSTEM, unrelated to
either feature's own on/off state (see services/system_health/monitor.py's
module docstring for why the whole feature is kept isolated from both).
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class HealthStatus:
    OK = "ok"                 # working, checked recently
    DEGRADED = "degraded"     # working but a warning condition (e.g. low credits)
    DOWN = "down"             # not working (invalid key/secret, auth failure)
    UNKNOWN = "unknown"       # never successfully checked yet
    ALL = (OK, DEGRADED, DOWN, UNKNOWN)


class HealthComponent:
    LLM = "llm"
    GRAPH = "graph"
    ALL = (LLM, GRAPH)


class SystemHealthCheck(Base):
    __tablename__ = "system_health_check"

    component: Mapped[str] = mapped_column(String, primary_key=True)  # HealthComponent.*
    status: Mapped[str] = mapped_column(String, nullable=False, default=HealthStatus.UNKNOWN)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_ok_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # When an alert email was last actually sent for this component's current
    # bad streak — gates re-alerting (see monitor.py's ALERT_RESEND_COOLDOWN)
    # so "still down" doesn't email again every single check tick.
    last_alert_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
