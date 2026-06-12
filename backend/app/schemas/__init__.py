"""API response schemas."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class AttachmentOut(BaseModel):
    attachment_id: str
    filename: str
    content_type: str
    kind: str


class EmailListItem(BaseModel):
    id: str
    provider_message_id: str
    sender_name: str | None
    sender_email: str | None
    subject: str | None
    received_at: datetime | None
    status: str
    attachment_count: int
    has_approval_screenshot: bool


class EmailDetail(EmailListItem):
    body_text: str | None
    attachments: list[AttachmentOut]


class DecisionIn(BaseModel):
    accepted: bool


class SourceFileEntry(BaseModel):
    key: str | None = None
    filename: str | None = None
    source_id: str | None = None
    attachment_id: str | None = None
    ingested_at: str | None = None
    buckets: dict[str, list[str]] = {}


class TimesheetOut(BaseModel):
    id: str
    employee_id: str | None
    employee_name: str | None
    account_manager: str | None
    dco_number: str | None
    match_note: str | None
    month: int
    year: int
    calendar_days: int | None
    annual_leave_dates: list[str]
    remote_work_dates: list[str]
    sick_leave_dates: list[str]
    unpaid_leave_dates: list[str]
    absent_dates: list[str]
    public_holiday_dates: list[str]
    annual_leave_count: int
    remote_work_count: int
    sick_leave_count: int
    unpaid_leave_count: int
    absent_count: int
    public_holiday_count: int
    validation_status: str
    llm_summary: str | None
    hr_flags: list[str]
    approval_detected: bool
    approval_detail: str | None
    approval_status: str
    source_email_id: str | None
    storage_folder: str | None
    source_files: list[SourceFileEntry] = []
    source_file_count: int = 0


class DashboardRow(BaseModel):
    employee_pk: str | None
    employee_id: str | None
    employee_name: str | None
    account_manager: str | None
    dco_number: str | None
    status: str          # "green" | "yellow"
    record_count: int
    needs_review_count: int
    pending_approval_count: int
    years: list[int]


class ApprovalIn(BaseModel):
    approved: bool


class TimesheetUpdate(BaseModel):
    """Edit the leave buckets / dates from the UI. Validation re-runs on save."""
    annual_leave_dates: list[str] | None = None
    remote_work_dates: list[str] | None = None
    sick_leave_dates: list[str] | None = None
    unpaid_leave_dates: list[str] | None = None
    absent_dates: list[str] | None = None
    public_holiday_dates: list[str] | None = None
    month: int | None = None
    year: int | None = None


class SourceFile(BaseModel):
    name: str
    rel_path: str
    content_type: str
    size: int


# ---- employee_matcher (all_employee_data) CRUD ----
class EmployeeIn(BaseModel):
    employee_id: str
    name: str
    dco_number: str | None = None
    account_manager: str | None = None
    employee_email_id: str | None = None
    project: str | None = None
    contact_no: str | None = None
    location: str | None = None
    all_emails: str | None = None


class EmployeeOut(EmployeeIn):
    id: str


class UploadResult(BaseModel):
    """One uploaded file's outcome. record_id is None when the file failed
    in the pipeline — pipeline_id always points at the tracker row."""
    pipeline_id: str
    filename: str
    status: str                       # success | needs_review | failed
    failure_code: str | None = None
    failure_detail: str | None = None
    record_id: str | None = None
    employee_name: str | None = None
    employee_id: str | None = None
    month: int | None = None
    year: int | None = None
    validation_status: str | None = None
    llm_summary: str | None = None
    match_note: str | None = None


# ---- pipeline tracker ----
class PipelineEvent(BaseModel):
    stage: str
    status: str          # ok | warn | fail
    detail: str
    at: str


class PipelineFileOut(BaseModel):
    id: str
    filename: str
    content_type: str | None
    size_bytes: int | None
    source_kind: str
    source_id: str | None
    attachment_id: str | None
    status: str
    stage: str
    failure_code: str | None
    failure_label: str | None
    failure_detail: str | None
    events: list[PipelineEvent]
    employee_id: str | None
    employee_name: str | None
    month: int | None
    year: int | None
    record_id: str | None
    can_retry: bool
    can_resolve_assign: bool
    resolved_at: datetime | None
    resolution_note: str | None
    created_at: datetime | None
    updated_at: datetime | None


class PipelineStats(BaseModel):
    total: int
    processing: int
    success: int
    needs_review: int
    failed: int
    resolved: int
    by_failure_code: dict[str, int]
    failure_labels: dict[str, str]


class PipelineResolveIn(BaseModel):
    note: str | None = None


class PipelineResolveAssignIn(BaseModel):
    employee_pk: str
    month: int
    year: int
    note: str | None = None


class SkipDetail(BaseModel):
    sheet: str
    row: int
    id: str
    name: str
    reason: str


class ImportSummary(BaseModel):
    inserted: int
    updated: int
    skipped: int
    skipped_details: list[SkipDetail] = []
