"""
Portal submission models — one employee's month, uploaded through the
self-service portal.

There is no manager login/approval step — the employee self-attests whether
manager approval already exists for the timesheet (`approval_claimed`,
required at Submit), and the INTERNAL timesheet team is the only real gate:
Accept in Compare & Fix files it, a "Send back" action (portal_extract's
sibling on api/routes/pipeline.py) rejects it with a note the employee sees
and can act on. `manager_decision`/`manager_note`/`decided_by` are kept as
the field names (no migration churn) but are now the INTERNAL reviewer's
send-back decision, not a manager's — `APPROVED` is never written by new
code; "accepted" is represented by the computed `review_state` below, not by
this field, since Accept deliberately never writes back to this table.

A submission holds up to 3 files (timesheet / sick_leave / other). Submitting
dispatches extraction in the BACKGROUND immediately (see
services/extract_email/portal_extract.py). Each file stages as its OWN
pipeline_files row (source_kind="portal", thread_key=
f"portal:{submission_id}:{kind}"), so the existing ingest_manual_entry union/
dedup machinery handles everything from here with zero new merge logic.

`review_state`/`record_id` are COMPUTED ON READ (by the portal status
endpoints), never written back by Accept — Accept stays exactly the shared
choke point it already is, with no portal-specific branch.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class PortalSubmissionStatus:
    DRAFT = "draft"
    SUBMITTED = "submitted"
    REJECTED = "rejected"
    ALL = (DRAFT, SUBMITTED, REJECTED)


class ManagerDecision:
    """Field/column names kept as-is (no migration) — this is now the
    INTERNAL reviewer's send-back decision. APPROVED is never written by new
    code: Accept doesn't touch this table at all (see module docstring), so
    "accepted" only ever shows up via the computed review_state, not here."""
    PENDING = "pending"
    NOT_APPROVED = "not_approved"
    ALL = (PENDING, NOT_APPROVED)


class ExtractionState:
    NOT_STARTED = "not_started"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    ALL = (NOT_STARTED, RUNNING, DONE, FAILED)


class SubmissionFileKind:
    TIMESHEET = "timesheet"
    SICK_LEAVE = "sick_leave"
    OTHER = "other"
    ALL = (TIMESHEET, SICK_LEAVE, OTHER)


class PortalSubmission(Base):
    __tablename__ = "portal_submissions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    employee_pk: Mapped[str] = mapped_column(String, index=True)
    month: Mapped[int] = mapped_column(Integer)
    year: Mapped[int] = mapped_column(Integer)

    status: Mapped[str] = mapped_column(String, default=PortalSubmissionStatus.DRAFT)
    # The employee's own mandatory yes/no answer, given at Submit: "does this
    # timesheet already carry manager approval evidence?" Feeds into the same
    # Compare & Fix "Manager approval" toggle every other source populates
    # (AI-detected signature for email/upload) — here it's a self-attestation
    # the reviewer sees and can still override, not an automatic file-er.
    approval_claimed: Mapped[bool] = mapped_column(Boolean, default=False)
    manager_decision: Mapped[str] = mapped_column(String, default=ManagerDecision.PENDING)
    manager_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String, nullable=True)  # auth_users.id of the internal reviewer
    employee_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    extraction_state: Mapped[str] = mapped_column(String, default=ExtractionState.NOT_STARTED)
    extraction_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Computed-on-read caches — see module docstring. Never trust these as the
    # source of truth; they're refreshed whenever a status endpoint runs.
    review_state: Mapped[str | None] = mapped_column(String, nullable=True)
    record_id: Mapped[str | None] = mapped_column(String, nullable=True)

    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class PortalSubmissionFile(Base):
    __tablename__ = "portal_submission_files"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    submission_id: Mapped[str] = mapped_column(String, index=True)
    kind: Mapped[str] = mapped_column(String)
    filename: Mapped[str] = mapped_column(String)
    content_type: Mapped[str | None] = mapped_column(String, nullable=True)
    stored_path: Mapped[str] = mapped_column(String)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
