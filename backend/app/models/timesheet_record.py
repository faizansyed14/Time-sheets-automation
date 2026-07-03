"""
TimesheetRecord — one employee's extracted leave data for one month.

This is the equivalent of the old `EmployeeMonthly`, reorganised around the
new email-driven flow. It carries:
  - identity (extracted + matched to the employee matcher)
  - canonical leave buckets (dates + counts)
  - validation_status (machine): verified | manual_review  -> green / yellow
  - llm_summary: short human-readable issues (bad dates, duplicates, ...)
  - manager approval evidence (from screenshot, LLM-detected) + human sign-off
  - where it was filed on disk
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class ValidationStatus:
    VERIFIED = "verified"            # green
    MANUAL_REVIEW = "manual_review"  # yellow


class ApprovalStatus:
    PENDING = "pending"
    APPROVED = "approved"
    NOT_APPROVED = "not_approved"


class TimesheetRecord(Base):
    __tablename__ = "timesheet_records"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)

    # ---- identity ----
    extracted_employee_id: Mapped[str | None] = mapped_column(String, nullable=True)
    extracted_employee_name: Mapped[str | None] = mapped_column(String, nullable=True)
    # FK to all_employee_data (string id). Null if no confident match.
    matched_employee_pk: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    employee_id: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    employee_name: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    account_manager: Mapped[str | None] = mapped_column(String, nullable=True)
    dco_number: Mapped[str | None] = mapped_column(String, nullable=True)
    match_note: Mapped[str | None] = mapped_column(String, nullable=True)

    month: Mapped[int] = mapped_column(Integer, index=True)
    year: Mapped[int] = mapped_column(Integer, index=True)
    calendar_days: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # ---- canonical leave buckets (lists of ISO date strings) ----
    annual_leave_dates: Mapped[list] = mapped_column(JSON, default=list)
    remote_work_dates: Mapped[list] = mapped_column(JSON, default=list)
    sick_leave_dates: Mapped[list] = mapped_column(JSON, default=list)
    unpaid_leave_dates: Mapped[list] = mapped_column(JSON, default=list)
    absent_dates: Mapped[list] = mapped_column(JSON, default=list)
    public_holiday_dates: Mapped[list] = mapped_column(JSON, default=list)

    # ---- machine validation ----
    validation_status: Mapped[str] = mapped_column(
        String, default=ValidationStatus.VERIFIED, index=True
    )
    llm_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    hr_flags: Mapped[list] = mapped_column(JSON, default=list)

    # ---- manager approval ----
    approval_detected: Mapped[bool] = mapped_column(Boolean, default=False)  # LLM read screenshot
    approval_detail: Mapped[str | None] = mapped_column(String, nullable=True)
    approval_status: Mapped[str] = mapped_column(
        String, default=ApprovalStatus.PENDING, index=True
    )  # human sign-off

    # ---- provenance ----
    source_email_id: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    storage_folder: Mapped[str | None] = mapped_column(String, nullable=True)

    # Every file that contributed to this month (weekly / 15-day timesheets).
    # Each entry keeps its own buckets so re-uploading the same file replaces
    # its contribution instead of duplicating it:
    # [{"key", "filename", "source_id", "attachment_id", "ingested_at",
    #   "buckets": {"annual": [...], "remote": [...], ...}}]
    source_files: Mapped[list] = mapped_column(JSON, default=list)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # convenience counts (computed on write)
    @property
    def annual_leave_count(self) -> int:
        return len(self.annual_leave_dates or [])

    @property
    def remote_work_count(self) -> int:
        return len(self.remote_work_dates or [])

    @property
    def sick_leave_count(self) -> int:
        return len(self.sick_leave_dates or [])

    @property
    def unpaid_leave_count(self) -> int:
        return len(self.unpaid_leave_dates or [])

    @property
    def absent_count(self) -> int:
        return len(self.absent_dates or [])

    @property
    def public_holiday_count(self) -> int:
        return len(self.public_holiday_dates or [])

    @property
    def source_file_count(self) -> int:
        return len(self.source_files or [])
