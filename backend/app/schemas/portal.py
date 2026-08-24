"""Portal (employee self-service) request/response schemas.

No manager role — approval is the internal timesheet team's job via Compare
& Fix (Accept, or the "Send back" action below), not a separate manager
portal login.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


# ---------------- portal auth ----------------
class PortalLoginIn(BaseModel):
    username: str
    password: str


class PortalUserOut(BaseModel):
    id: str
    username: str
    role: str
    employee_pk: str | None = None
    employee_name: str | None = None  # resolved from Employee for display
    employee_id: str | None = None
    is_active: bool
    last_login_at: datetime | None = None


class PortalTokenResult(BaseModel):
    access_token: str
    user: PortalUserOut


# ---------------- admin: portal users ----------------
class AdminPortalUserCreate(BaseModel):
    username: str
    password: str
    employee_pk: str


class AdminPortalUserUpdate(BaseModel):
    is_active: bool | None = None
    password: str | None = None


# ---------------- submissions ----------------
class PortalSubmissionFileOut(BaseModel):
    id: str
    kind: str
    filename: str
    content_type: str | None = None
    size_bytes: int | None = None
    created_at: datetime


class PortalSubmissionOut(BaseModel):
    id: str
    employee_pk: str
    employee_name: str | None = None
    employee_id: str | None = None
    month: int
    year: int
    status: str
    # The employee's own mandatory yes/no answer at Submit — does this
    # timesheet already carry manager approval evidence? Feeds the SAME
    # Compare & Fix "Manager approval" toggle every other source populates.
    approval_claimed: bool = False
    manager_decision: str
    manager_note: str | None = None
    decided_at: datetime | None = None
    employee_note: str | None = None
    extraction_state: str
    extraction_error: str | None = None
    review_state: str | None = None
    record_id: str | None = None
    submitted_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    files: list[PortalSubmissionFileOut] = []


class PortalSubmissionPrecheckOut(BaseModel):
    submission: PortalSubmissionOut | None = None
    already_filed_elsewhere: bool = False
    filed_source_note: str | None = None


class PortalSubmissionUpsertIn(BaseModel):
    month: int
    year: int
    employee_note: str | None = None


class PortalSubmitIn(BaseModel):
    """approval_claimed has no default — Pydantic makes it a required field,
    so /submit 422s if the employee's client didn't ask (and answer) the
    mandatory yes/no question."""
    approval_claimed: bool


# ---------------- roster: every portal-enabled employee for ONE month ----------------
class PortalRosterMemberOut(BaseModel):
    """One employee, for one specific (month, year) — present whether or not
    they submitted THAT period, so missing submissions are as visible as
    filed ones. Deliberately period-scoped, not "their latest ever": an
    unscoped roster shows the same employee once per month they've ever
    submitted, which reads as duplicate rows for one person."""
    employee_pk: str
    employee_id: str
    employee_name: str
    has_portal_account: bool
    submission: PortalSubmissionOut | None = None


# ---------------- internal "Portal Submissions" view ----------------
class PortalSubmissionPipelineFileRef(BaseModel):
    """One slot's linked pipeline_files row (computed on read) — lets the
    internal Portal Submissions tab jump straight into Compare & Fix.
    record_id set means THIS file has already been accepted into a filed
    TimesheetRecord — nothing left to review for it."""
    kind: str
    pipeline_file_id: str | None = None
    pipeline_status: str | None = None
    record_id: str | None = None


class PortalSubmissionAdminOut(PortalSubmissionOut):
    pipeline_files: list[PortalSubmissionPipelineFileRef] = []


class PortalRosterMemberAdminOut(PortalRosterMemberOut):
    """Same period-scoped roster row as PortalRosterMemberOut, but carrying
    pipeline_files too — the internal Pipeline page's roster needs the
    Compare & Fix link."""
    submission: PortalSubmissionAdminOut | None = None


class PortalSendBackIn(BaseModel):
    """The internal reviewer's "Send back" action (Compare & Fix, portal
    items only) — always a rejection with a required note, distinct from the
    existing Delete button, which stays a silent discard for every source."""
    note: str
