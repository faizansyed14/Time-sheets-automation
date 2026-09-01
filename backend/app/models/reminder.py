"""
Timesheet reminder service.

  ReminderConfig — a single row (id="singleton") holding the ON/OFF switch for
    the automatic monthly run. The schedule itself (28th, 9am UAE) is fixed by
    product decision, not admin-editable, so it isn't a column here — see
    services/reminders/service.py's SEND_DAY/SEND_HOUR_UAE constants. Only the
    toggle is persisted/mutable.

  ReminderRun — one batch (either the automatic 28th run, or an admin's manual
    "Run for all now") — a row per invocation, with roll-up counts, so the
    Reminders page can show run history without recomputing it from the logs
    every time.

  ReminderLog — one row per email actually attempted (scheduled batch, manual
    batch, single "Send now", or a test send). This is BOTH the audit trail
    and the duplicate-prevention source of truth: "has employee X already been
    sent a reminder for month/year Y" is answered by querying this table, not
    by a DB unique constraint — a manual resend must still be possible (with a
    confirmation), so uniqueness is enforced in application logic
    (service.already_sent), never at the schema level.

No FK constraints against all_employee_data — matching every other table in
this codebase that references an employee (TimesheetRecord.matched_employee_pk,
PortalSubmission.employee_pk, ...); identity is a looked-up string, not a
DB-level relationship.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class ReminderTrigger:
    SCHEDULED = "scheduled"        # the automatic 28th/9am UAE run
    MANUAL_BATCH = "manual_batch"  # admin's "Run for all now"
    MANUAL_SINGLE = "manual"       # per-employee "Send now"
    TEST = "test"                  # arbitrary test-address send
    ALL = (SCHEDULED, MANUAL_BATCH, MANUAL_SINGLE, TEST)


class ReminderStatus:
    SENT = "sent"
    FAILED = "failed"
    SKIPPED = "skipped"   # already sent for this employee/month — batch runs only
    ALL = (SENT, FAILED, SKIPPED)


class EmailPreference:
    """Which of an employee's two separate addresses (Employee.work_email /
    Employee.personal_email) reminders send to. A single global choice
    (not per-employee) — see services/reminders/service.py's resolve_email
    for the fallback order when the preferred one is blank for a given
    person."""
    WORK = "work"
    PERSONAL = "personal"
    ALL = (WORK, PERSONAL)


class ReminderConfig(Base):
    __tablename__ = "reminder_config"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: "singleton")
    auto_send_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    email_preference: Mapped[str] = mapped_column(
        String, nullable=False, default=EmailPreference.WORK, server_default=EmailPreference.WORK)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    updated_by: Mapped[str | None] = mapped_column(String, nullable=True)


class ReminderRun(Base):
    __tablename__ = "reminder_runs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    trigger: Mapped[str] = mapped_column(String, index=True)  # ReminderTrigger.SCHEDULED | MANUAL_BATCH
    month: Mapped[int] = mapped_column(Integer)
    year: Mapped[int] = mapped_column(Integer)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    total: Mapped[int] = mapped_column(Integer, default=0)
    sent_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)
    skipped_count: Mapped[int] = mapped_column(Integer, default=0)
    triggered_by: Mapped[str | None] = mapped_column(String, nullable=True)  # username; null for the scheduled run


class ReminderLog(Base):
    __tablename__ = "reminder_logs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    run_id: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    employee_pk: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    employee_id: Mapped[str | None] = mapped_column(String, nullable=True)
    employee_name: Mapped[str | None] = mapped_column(String, nullable=True)
    recipient_email: Mapped[str | None] = mapped_column(String, nullable=True)
    month: Mapped[int] = mapped_column(Integer, index=True)
    year: Mapped[int] = mapped_column(Integer, index=True)
    trigger: Mapped[str] = mapped_column(String, index=True)
    status: Mapped[str] = mapped_column(String, index=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
