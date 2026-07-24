"""API response schemas."""
from __future__ import annotations

from datetime import datetime
from typing import Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    """A paginated slice of a larger result set. `has_more` drives the
    frontend's infinite scroll (keep loading the next offset until false)."""
    items: list[T]
    total: int          # total rows matching the filter/search (whole DB)
    limit: int
    offset: int
    has_more: bool


class EmailRecipient(BaseModel):
    name: str | None = None
    email: str


class AttachmentOut(BaseModel):
    attachment_id: str
    filename: str
    content_type: str
    kind: str
    cid: str | None = None
    is_inline: bool | None = None
    size: int | None = None


class MatchedEmployeeOut(BaseModel):
    employee_pk: str
    employee_id: str
    employee_name: str
    account_manager: str | None = None
    location: str | None = None
    matched_email: str | None = None
    is_sender: bool = False
    source: str | None = None


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
    extract_email_at: datetime | None = None  # last Extract Email run (any pipeline item)
    no_sheets_found_at: datetime | None = None  # last run found NOTHING to stage
    no_sheets_note: str | None = None
    attachments: list[AttachmentOut] = []
    # Outlook-style conversation grouping. thread_id is always set (falls back
    # to this message's own id when the provider gave no conversation_id, so
    # every message is at least its own singleton thread).
    conversation_id: str | None = None
    thread_id: str | None = None
    thread_message_count: int = 1


class EmailDetail(EmailListItem):
    body_text: str | None
    body_html: str | None = None
    to_recipients: list[EmailRecipient] = []
    cc_recipients: list[EmailRecipient] = []
    # Attachment ids embedded inline in body_html (resolved to data URIs) —
    # hidden from the separate attachment list, as Outlook does.
    inline_attachment_ids: list[str] = []


class ThreadListItem(EmailListItem):
    """One row per conversation in the threaded inbox list — the newest
    message's summary, plus how many messages the thread has."""
    pass


class ThreadDetail(BaseModel):
    """Every message in a conversation, oldest first — the Outlook-style
    conversation view."""
    thread_id: str
    messages: list[EmailDetail]


# ---- agentic chat ----
class ChatMessageIn(BaseModel):
    role: str            # "user" | "assistant"
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessageIn]


class ChatChange(BaseModel):
    record_id: str
    employee_name: str | None = None
    month: int
    year: int
    month_name: str | None = None
    leave_type: str
    action: str          # add | set | clear
    before: list[str] = []
    after: list[str] = []
    added: list[str] = []
    removed: list[str] = []


class ChatResponse(BaseModel):
    answer: str
    changes: list[ChatChange] = []
    tools_used: list[str] = []
    error: str | None = None


class ChatPromptGroup(BaseModel):
    group: str
    prompts: list[str]


class ChatSuggestions(BaseModel):
    suggestions: list[str]
    prompt_book: list[ChatPromptGroup]
    enabled: bool          # whether an AI provider is configured
    model: str | None = None


class DecisionIn(BaseModel):
    """Archive (accepted=False). Direct accept-and-ingest was removed — every
    extraction goes through Extract Email + Compare & Fix review."""
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
    matched_employee_pk: str | None = None
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
    maternity_leave_dates: list[str]
    unpaid_leave_dates: list[str]
    absent_dates: list[str]
    public_holiday_dates: list[str]
    annual_leave_count: int
    remote_work_count: int
    sick_leave_count: int
    maternity_leave_count: int
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


class TimesheetExportOut(TimesheetOut):
    """Timesheet row plus matcher fields for the Export page / XLSX."""
    location: str | None = None
    project: str | None = None
    employee_email: str | None = None
    contact_no: str | None = None
    has_record: bool = True


class DashboardRow(BaseModel):
    employee_pk: str | None
    employee_id: str | None
    employee_name: str | None
    account_manager: str | None
    dco_number: str | None
    location: str | None = None
    status: str          # "green" | "yellow"
    record_count: int
    needs_review_count: int
    pending_approval_count: int
    years: list[int]
    # Coverage: which months (1-12) this employee submitted in the focus year,
    # whether they are a matcher employee, and whether they have any record yet.
    submitted_months: list[int] = []
    in_matcher: bool = True
    has_records: bool = True
    # Record for the dashboard's focus month+year (one-click open from the table).
    focus_record_id: str | None = None
    focus_validation_status: str | None = None
    focus_approval_status: str | None = None


class DashboardSummary(BaseModel):
    year: int
    month: int                       # focus month for the "missing" figure
    total_employees: int             # employees in the matcher (global)
    submitted_this_month: int        # global
    missing_this_month: int          # global
    needs_review: int                # employees with at least one flagged record (global)
    pending_approval: int            # employees with at least one unapproved record (global)
    missing_employees: list[str] = []  # sample of names missing the focus month
    # ---- paginated rows (infinite scroll) ----
    rows: list[DashboardRow] = []
    filtered_total: int = 0          # rows matching the q / location / only_missing filter
    limit: int = 200
    offset: int = 0
    has_more: bool = False


class ApprovalIn(BaseModel):
    approved: bool


class TimesheetUpdate(BaseModel):
    """Edit the leave buckets / dates from the UI. Validation re-runs on save."""
    annual_leave_dates: list[str] | None = None
    remote_work_dates: list[str] | None = None
    sick_leave_dates: list[str] | None = None
    maternity_leave_dates: list[str] | None = None
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
    extraction_model: str | None = None
    extraction_method: str | None = None
    used_ocr: bool = False
    extraction_meta: dict | None = None
    # True when the record was filed by the AI without human review (high
    # confidence + full verification). See extraction_meta.auto_accept for why.
    auto_accepted: bool = False
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
