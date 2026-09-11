"""
Pipeline tracker routes — full visibility of every file that entered the
extraction pipeline: where it is, where it failed and why, plus Review
(manual fix / correct a filed record) and Retry (re-run after fixing the cause).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import json as _json

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_full_access
from app.core import datacache
from app.core.http_headers import content_disposition
from app.core.database import get_db
from app.models.auth import User
from app.models.pipeline_file import FailureCode, PipelineFile, PipelineStage, PipelineStatus
from app.schemas import Page, PipelineFileOut, PipelineStats
from app.schemas.portal import (
    PortalRosterMemberAdminOut,
    PortalSendBackIn,
    PortalSubmissionAdminOut,
    PortalSubmissionFileOut,
    PortalSubmissionPipelineFileRef,
)
from app.services.pipeline.ingestion import can_resolve_assign, retry_pipeline_file

router = APIRouter(prefix="/pipeline", tags=["pipeline"])

# Human-readable labels the UI can show next to each failure code.
FAILURE_LABELS: dict[str, str] = {
    FailureCode.PROTECTED_PDF: "Protected PDF",
    FailureCode.UNSUPPORTED_TYPE: "Unsupported file type",
    FailureCode.EMPTY_FILE: "Empty file",
    FailureCode.LLM_FAILED: "LLM extraction failed",
    FailureCode.EXTRACTION_UNREADABLE: "Sheet unreadable",
    FailureCode.NAME_NOT_FOUND: "Name not found",
    FailureCode.MONTH_NOT_FOUND: "Month not found",
    FailureCode.EMPLOYEE_NOT_MATCHED: "Employee not in matcher",
    FailureCode.AMBIGUOUS_ID: "Ambiguous employee ID",
    FailureCode.ID_NAME_MISMATCH: "ID / name mismatch",
    FailureCode.VALIDATION_MISMATCH: "Validation mismatch",
    FailureCode.STORAGE_ERROR: "Storage error",
    FailureCode.DUPLICATE_FILE: "Duplicate file",
    FailureCode.PENDING_REVIEW: "Awaiting review",
    FailureCode.RECORD_DELETED: "Record deleted",
    FailureCode.UNKNOWN: "Unknown error",
}


def _out(t: PipelineFile) -> PipelineFileOut:
    return PipelineFileOut(
        id=t.id, filename=t.filename, content_type=t.content_type, size_bytes=t.size_bytes,
        source_kind=t.source_kind, source_id=t.source_id, attachment_id=t.attachment_id,
        status=t.status, stage=t.stage, failure_code=t.failure_code,
        failure_label=FAILURE_LABELS.get(t.failure_code or "", None),
        failure_detail=t.failure_detail, events=t.events or [],
        employee_id=t.employee_id, employee_name=t.employee_name,
        month=t.month, year=t.year, record_id=t.record_id,
        extraction_model=t.extraction_model, extraction_method=t.extraction_method,
        used_ocr=bool(t.used_ocr), extraction_meta=t.extraction_meta,
        auto_accepted=bool((t.extraction_meta or {}).get("auto_accept", {}).get("accepted")),
        can_retry=bool(t.raw_path or (t.source_kind == "email" and t.attachment_id)),
        can_resolve_assign=can_resolve_assign(t),
        resolved_at=t.resolved_at, resolution_note=t.resolution_note,
        created_at=t.created_at, updated_at=t.updated_at,
    )


@router.get("", response_model=Page[PipelineFileOut])
async def list_pipeline_files(
    status: str | None = Query(default=None, description="processing|success|needs_review|failed|resolved"),
    exclude_status: str | None = Query(
        default=None,
        description="Hide one or more statuses from the results, comma-separated "
                    "(the Activity log hides 'success,resolved' by default — "
                    "finished work isn't what the page is for)"),
    failure_code: str | None = Query(default=None),
    source_kind: str | None = Query(default=None, description="upload|email|manual|portal"),
    source_id: str | None = Query(default=None, description="Filter by PipelineFile.source_id"),
    thread_key: str | None = Query(
        default=None,
        description="Filter by PipelineFile.thread_key (conversation) — every "
                     "record staged from this email thread, however many "
                     "employee+month groups it produced"),
    auto_accepted: bool | None = Query(
        default=None, description="true = AI recommends accept (not yet filed)"),
    month: int | None = Query(default=None, ge=1, le=12, description="Filter by PipelineFile.month"),
    year: int | None = Query(default=None, ge=2000, le=2100, description="Filter by PipelineFile.year"),
    updated_after: datetime | None = Query(
        default=None,
        description="Only rows whose PipelineFile.updated_at is at/after this instant — "
                     "backs the Activity page's scoped 'Success (last 30 days)' card, so "
                     "clicking it lists exactly the rows the card counted, not every "
                     "success ever."),
    q: str | None = Query(default=None, description="search filename / employee (whole table)"),
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """Paginated pipeline tracker. Filters + search run in SQL across the whole
    table so scrolling/searching never misses rows beyond the current page."""
    base = select(PipelineFile)
    if status:
        base = base.where(PipelineFile.status == status)
    if exclude_status:
        excluded = [s.strip() for s in exclude_status.split(",") if s.strip()]
        if excluded:
            base = base.where(PipelineFile.status.not_in(excluded))
    if failure_code:
        base = base.where(PipelineFile.failure_code == failure_code)
    if source_kind:
        base = base.where(PipelineFile.source_kind == source_kind)
    if source_id:
        base = base.where(PipelineFile.source_id == source_id)
    if thread_key:
        base = base.where(PipelineFile.thread_key == thread_key)
    if month:
        base = base.where(PipelineFile.month == month)
    if year:
        base = base.where(PipelineFile.year == year)
    if updated_after:
        base = base.where(PipelineFile.updated_at >= updated_after)
    if auto_accepted is not None:
        # Filter in SQL, not on the page: successes are mostly human accepts,
        # so client-side filtering would drop auto-accepts past the first page.
        flag = PipelineFile.extraction_meta["auto_accept"]["accepted"].as_boolean()
        base = base.where(flag.is_(True) if auto_accepted else
                          or_(flag.is_(False), flag.is_(None)))
    if q and q.strip():
        like = f"%{q.strip().lower()}%"
        base = base.where(or_(
            func.lower(PipelineFile.filename).like(like),
            func.lower(PipelineFile.employee_name).like(like),
            func.lower(PipelineFile.employee_id).like(like),
        ))

    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    rows = (await db.execute(
        base.order_by(PipelineFile.created_at.desc()).limit(limit).offset(offset)
    )).scalars().all()
    return Page(items=[_out(t) for t in rows], total=total, limit=limit, offset=offset,
                has_more=offset + len(rows) < total)


@router.get("/stats", response_model=PipelineStats)
async def pipeline_stats(
    success_window_days: int = Query(default=60, ge=1, le=365,
        description="How far back success_recent counts — the Activity page's "
                    "'Success' stat card lets the user pick 60/90/120 days."),
    db: AsyncSession = Depends(get_db),
):
    async def _compute() -> dict:
        rows = (await db.execute(select(PipelineFile))).scalars().all()
        by_status: dict[str, int] = {}
        by_failure: dict[str, int] = {}
        # "success" (below) is the all-time total — needed as-is for the
        # Activity page's "Open files" math (total - success - resolved), so
        # it must never be scoped down. success_recent is a SEPARATE, purely
        # additive figure: an all-time count only grows forever and stops
        # being a meaningful "at a glance" health indicator after the app has
        # been in use for months/years — this is what the "Success" stat
        # CARD actually displays instead, over a window the user can widen.
        recent_cutoff = datetime.now(timezone.utc) - timedelta(days=success_window_days)
        success_recent = 0
        for t in rows:
            by_status[t.status] = by_status.get(t.status, 0) + 1
            if t.status in (PipelineStatus.FAILED, PipelineStatus.NEEDS_REVIEW) and t.failure_code:
                by_failure[t.failure_code] = by_failure.get(t.failure_code, 0) + 1
            if t.status == PipelineStatus.SUCCESS and t.updated_at and t.updated_at >= recent_cutoff:
                success_recent += 1
        return {
            "total": len(rows),
            "processing": by_status.get(PipelineStatus.PROCESSING, 0),
            "success": by_status.get(PipelineStatus.SUCCESS, 0),
            "success_recent": success_recent,
            "needs_review": by_status.get(PipelineStatus.NEEDS_REVIEW, 0),
            "failed": by_status.get(PipelineStatus.FAILED, 0),
            "resolved": by_status.get(PipelineStatus.RESOLVED, 0),
            "by_failure_code": by_failure,
            "failure_labels": FAILURE_LABELS,
        }

    # Cached (short TTL) — the UI polls this every 15s from several screens.
    # Keyed by the window too: otherwise switching 60 -> 90 days inside the
    # TTL would silently hand back the OTHER window's cached success_recent.
    data = await datacache.get_or_set(
        datacache.NS_PIPELINE, f"stats:{success_window_days}", datacache.TTL_STATS, _compute)
    return PipelineStats(**data)


async def _portal_submission_admin_rows(db: AsyncSession, subs: list) -> list:
    """Shared row-assembly for both the list and single-submission admin
    views — review_state/record_id/pipeline_files are all computed HERE, on
    read, never written back by Accept (see module docstring on the list
    route below for why)."""
    from app.models.employee import Employee
    from app.models.portal_submission import PortalSubmissionFile

    if not subs:
        return []
    emp_pks = {s.employee_pk for s in subs}
    employees = {e.id: e for e in (await db.execute(
        select(Employee).where(Employee.id.in_(emp_pks)))).scalars().all()}

    sub_ids = [s.id for s in subs]
    # Every PipelineFile a portal submission staged uses source_id
    # "portal:<submission_id>:<kind>" — LIKE-match per submission id, since
    # there's no FK (this repo's identity-by-lookup convention, not a schema
    # gap — see the migration's own comment on why).
    all_files = (await db.execute(select(PipelineFile).where(
        PipelineFile.source_kind == "portal",
        or_(*[PipelineFile.source_id.like(f"portal:{sid}:%") for sid in sub_ids]),
    ))).scalars().all()
    files_by_sub: dict[str, list[PipelineFile]] = {}
    for f in all_files:
        # "portal:<sub_id>:<kind>" -> sub_id (kind itself may contain no colons)
        parts = (f.source_id or "").split(":", 2)
        sid = parts[1] if len(parts) == 3 else None
        if sid:
            files_by_sub.setdefault(sid, []).append(f)

    out: list[PortalSubmissionAdminOut] = []
    for s in subs:
        emp = employees.get(s.employee_pk)
        files = (await db.execute(select(PortalSubmissionFile).where(
            PortalSubmissionFile.submission_id == s.id))).scalars().all()
        linked = files_by_sub.get(s.id, [])
        record_id = next((f.record_id for f in linked if f.record_id), None)
        if linked:
            statuses = {f.status for f in linked}
            review_state = ("accepted" if record_id and all(f.record_id for f in linked)
                            else "needs_review" if PipelineStatus.NEEDS_REVIEW in statuses
                            else "success" if statuses == {PipelineStatus.SUCCESS}
                            else "processing")
        else:
            review_state = "pending" if s.extraction_state != "done" else "no_files_staged"
        out.append(PortalSubmissionAdminOut(
            id=s.id, employee_pk=s.employee_pk,
            employee_name=emp.name if emp else None, employee_id=emp.employee_id if emp else None,
            month=s.month, year=s.year, status=s.status,
            approval_claimed=s.approval_claimed,
            manager_decision=s.manager_decision, manager_note=s.manager_note,
            decided_at=s.decided_at, employee_note=s.employee_note,
            extraction_state=s.extraction_state, extraction_error=s.extraction_error,
            review_state=review_state, record_id=record_id,
            submitted_at=s.submitted_at, created_at=s.created_at, updated_at=s.updated_at,
            files=[PortalSubmissionFileOut(id=f.id, kind=f.kind, filename=f.filename,
                                            content_type=f.content_type, size_bytes=f.size_bytes,
                                            created_at=f.created_at) for f in files],
            pipeline_files=[PortalSubmissionPipelineFileRef(
                kind=(f.source_id or "").rsplit(":", 1)[-1],
                pipeline_file_id=f.id, pipeline_status=f.status,
                record_id=f.record_id) for f in linked],
        ))
    return out


@router.get("/portal-submissions", response_model=list[PortalSubmissionAdminOut])
async def list_portal_submissions(
    manager_decision: str | None = Query(
        default=None,
        description="pending|not_approved — \"accepted\" isn't tracked here, see review_state instead"),
    month: int | None = Query(default=None, ge=1, le=12),
    year: int | None = Query(default=None, ge=2000),
    db: AsyncSession = Depends(get_db),
):
    """The internal 'Portal Submissions' view — browse portal activity by
    manager-decision status (a dimension that lives on PortalSubmission, not
    on individual PipelineFile rows). review_state/record_id/pipeline_files
    are all computed HERE, on read — Accept never writes back to
    PortalSubmission, so this endpoint is the only place that link exists."""
    from app.models.portal_submission import PortalSubmission

    base = select(PortalSubmission).where(PortalSubmission.status != "draft")
    if manager_decision:
        base = base.where(PortalSubmission.manager_decision == manager_decision)
    if month:
        base = base.where(PortalSubmission.month == month)
    if year:
        base = base.where(PortalSubmission.year == year)
    subs = (await db.execute(base.order_by(PortalSubmission.submitted_at.desc()))).scalars().all()
    return await _portal_submission_admin_rows(db, subs)


@router.get("/portal-submissions/{submission_id}", response_model=PortalSubmissionAdminOut)
async def get_portal_submission(submission_id: str, db: AsyncSession = Depends(get_db)):
    """One submission's live, current state."""
    from app.models.portal_submission import PortalSubmission

    sub = (await db.execute(select(PortalSubmission).where(
        PortalSubmission.id == submission_id))).scalar_one_or_none()
    if not sub:
        raise HTTPException(404, "Submission not found")
    rows = await _portal_submission_admin_rows(db, [sub])
    return rows[0]


@router.post("/portal-submissions/{submission_id}/send-back", response_model=PortalSubmissionAdminOut)
async def send_back_portal_submission(
    submission_id: str, body: PortalSendBackIn,
    reviewer: User = Depends(require_full_access), db: AsyncSession = Depends(get_db),
):
    """The internal reviewer's rejection — distinct from the existing Delete
    button (which stays a silent discard for every source). Requires a note,
    sets the submission back to "rejected" so the employee sees exactly why
    and can fix + resend (upload_file/delete_file already re-queue it
    automatically once they do — see portal_employee.py). Never touches the
    staged PipelineFile row(s) themselves; a reviewer who wants this off
    their plate entirely can still use the ordinary Delete button too."""
    from app.models.portal_submission import ManagerDecision, PortalSubmission, PortalSubmissionStatus

    if not body.note.strip():
        raise HTTPException(400, "A note is required so the employee knows what to fix.")
    sub = (await db.execute(select(PortalSubmission).where(
        PortalSubmission.id == submission_id))).scalar_one_or_none()
    if not sub:
        raise HTTPException(404, "Submission not found")

    sub.status = PortalSubmissionStatus.REJECTED
    sub.manager_decision = ManagerDecision.NOT_APPROVED
    sub.manager_note = body.note.strip()
    sub.decided_at = datetime.now(timezone.utc)
    sub.decided_by = reviewer.id
    await db.commit()
    await db.refresh(sub)
    rows = await _portal_submission_admin_rows(db, [sub])
    return rows[0]


@router.get("/portal-roster", response_model=list[PortalRosterMemberAdminOut])
async def portal_roster(
    month: int = Query(ge=1, le=12), year: int = Query(ge=2000),
    db: AsyncSession = Depends(get_db),
):
    """Every employee with a portal login, for ONE month/year — submitted or
    not, so 'who's missing' is as visible as 'who's already sent it in.'
    Backs the internal Pipeline page's Portal Submissions tab."""
    from app.models.employee import Employee
    from app.models.portal_auth import PortalRole, PortalUser
    from app.models.portal_submission import PortalSubmission, PortalSubmissionStatus

    accounts = (await db.execute(select(PortalUser).where(
        PortalUser.role == PortalRole.EMPLOYEE, PortalUser.employee_pk.isnot(None),
    ))).scalars().all()
    emp_pks = [a.employee_pk for a in accounts if a.employee_pk]
    if not emp_pks:
        return []
    employees = {e.id: e for e in (await db.execute(
        select(Employee).where(Employee.id.in_(emp_pks)))).scalars().all()}

    subs = (await db.execute(select(PortalSubmission).where(
        PortalSubmission.employee_pk.in_(emp_pks),
        PortalSubmission.month == month, PortalSubmission.year == year,
        PortalSubmission.status != PortalSubmissionStatus.DRAFT,
    ))).scalars().all()
    admin_rows = {r.employee_pk: r for r in await _portal_submission_admin_rows(db, subs)}

    out: list[PortalRosterMemberAdminOut] = []
    for pk in emp_pks:
        emp = employees.get(pk)
        if not emp:
            continue
        out.append(PortalRosterMemberAdminOut(
            employee_pk=emp.id, employee_id=emp.employee_id, employee_name=emp.name,
            has_portal_account=True, submission=admin_rows.get(pk),
        ))
    out.sort(key=lambda m: m.employee_name.lower())
    return out


@router.get("/portal-submissions/{submission_id}/files/{kind}/content")
async def portal_submission_file_content(submission_id: str, kind: str, db: AsyncSession = Depends(get_db)):
    """Preview a portal submission's uploaded file straight from
    portal_store — works even before/if extraction ever staged anything, so
    a reviewer isn't blocked from looking at what was sent in just because
    extraction failed or hasn't run yet."""
    from app.models.portal_submission import PortalSubmissionFile
    from app.services.pipeline import portal_store

    f = (await db.execute(select(PortalSubmissionFile).where(
        PortalSubmissionFile.submission_id == submission_id, PortalSubmissionFile.kind == kind,
    ))).scalar_one_or_none()
    if not f:
        raise HTTPException(404, "File not found")
    data = portal_store.read_portal_file(f.stored_path)
    if not data:
        raise HTTPException(404, "File is no longer available")
    return portal_store.content_response(f.filename, f.content_type, data)


@router.get("/portal-submissions/{submission_id}/files/{kind}/render")
async def portal_submission_file_render(submission_id: str, kind: str,
                                        page: int = Query(default=1, ge=1, le=50),
                                        db: AsyncSession = Depends(get_db)):
    from app.models.portal_submission import PortalSubmissionFile
    from app.services.pipeline import portal_store

    f = (await db.execute(select(PortalSubmissionFile).where(
        PortalSubmissionFile.submission_id == submission_id, PortalSubmissionFile.kind == kind,
    ))).scalar_one_or_none()
    if not f:
        raise HTTPException(404, "File not found")
    data = portal_store.read_portal_file(f.stored_path)
    if not data:
        raise HTTPException(404, "File is no longer available")
    return portal_store.render_response(f.filename, data, page)


@router.post("/rematch-unmatched")
async def rematch_unmatched_pipeline(db: AsyncSession = Depends(get_db)):
    """Re-check employee identity for every still-under-review pipeline item
    whose employee wasn't found at staging time — catches a roster/email
    that was staged BEFORE that employee existed in the Employee Matcher
    (matching only ever runs once, at staging time; nothing re-checks an
    already-staged item later on its own). Reuses the name/ID already
    captured — no re-extraction, no LLM call — so unlike /retry (a full
    vision re-extraction, one file at a time, and not built for bulk-roster
    items anyway) this is safe and cheap to run over the whole backlog at
    once. Never overwrites an already-matched or already-decided item, and
    never guesses — anything still genuinely unmatched or ambiguous is left
    exactly as it was."""
    from app.services.pipeline.rematch import rematch_unmatched

    result = await rematch_unmatched(db)
    await datacache.bust_pipeline()
    return result


@router.post("/{pipeline_id}/retry", response_model=PipelineFileOut)
async def retry_pipeline(pipeline_id: str, db: AsyncSession = Depends(get_db)):
    """Re-run the pipeline on the stored copy of the file (e.g. after adding
    the missing employee to the matcher, or fixing the LLM key)."""
    t = (await db.execute(select(PipelineFile).where(PipelineFile.id == pipeline_id))).scalar_one_or_none()
    if not t:
        raise HTTPException(404, "Pipeline file not found")
    try:
        _rec, t = await retry_pipeline_file(db, t)
    except FileNotFoundError as e:
        raise HTTPException(409, str(e))
    except ValueError as e:
        raise HTTPException(422, str(e))
    await datacache.bust_pipeline()
    return _out(t)


@router.delete("/{pipeline_id}")
async def delete_pipeline_file(pipeline_id: str, db: AsyncSession = Depends(get_db)):
    t = (await db.execute(select(PipelineFile).where(PipelineFile.id == pipeline_id))).scalar_one_or_none()
    if not t:
        raise HTTPException(404, "Pipeline file not found")
    # Remove the retry copy (S3 or local) before dropping the tracker row.
    from app.services.pipeline.ingestion import purge_raw_copy
    purge_raw_copy(t)
    await db.delete(t)
    await db.commit()
    await datacache.bust_pipeline()
    return {"deleted": pipeline_id}


_MANUAL_BUCKETS = (
    "annual", "remote", "sick", "maternity", "unpaid", "absent", "public_holiday", "other",
    "working", "weekend",
)


@router.post("/{pipeline_id}/manual-fix", response_model=PipelineFileOut)
async def pipeline_manual_fix(
    pipeline_id: str,
    employee_pk: str = Form(...),
    month: int = Form(...),
    year: int = Form(...),
    buckets: str = Form("{}"),
    note: str | None = Form(default=None),
    approval_status: str | None = Form(default=None),   # "approved" | "not_approved"
    approval_detail: str | None = Form(default=None),
    files: list[UploadFile] = File(default=[]),
    db: AsyncSession = Depends(get_db),
):
    """Resolve a failed/needs-review file by manually entering leave data.

    Identical to /upload/manual but re-uses the existing pipeline tracker
    instead of creating a new one. Raw copy is purged on success.
    """
    t = (await db.execute(select(PipelineFile).where(PipelineFile.id == pipeline_id))).scalar_one_or_none()
    if not t:
        raise HTTPException(404, "Pipeline file not found")
    if t.status not in (PipelineStatus.FAILED, PipelineStatus.NEEDS_REVIEW):
        raise HTTPException(409, f"Only failed/needs-review files can be fixed (status: '{t.status}').")
    if not (1 <= month <= 12 and year >= 2000):
        raise HTTPException(400, "Invalid month or year.")

    try:
        parsed = _json.loads(buckets or "{}")
        if not isinstance(parsed, dict):
            raise ValueError
    except Exception:
        raise HTTPException(400, "`buckets` must be a JSON object of bucket -> date list.")
    bucket_data = {
        b: [str(d).strip() for d in (parsed.get(b) or []) if str(d).strip()]
        for b in _MANUAL_BUCKETS
    }

    attachments: list[tuple[str, str, bytes]] = []
    for f in files or []:
        data = await f.read()
        if data:
            attachments.append((f.filename or "attachment",
                                 f.content_type or "application/octet-stream", data))

    from app.services.pipeline.ingestion import (
        ingest_manual_entry, isolate_employee_attachments, purge_raw_copy, read_raw_copy,
    )

    # If the reviewer didn't attach a replacement file, carry the original raw
    # file forward as the record attachment so it is stored in the File Vault.
    if not attachments and t.raw_path:
        raw_bytes = read_raw_copy(t)
        if raw_bytes:
            # One email carrying SEVERAL employees' sheets (a manager sending
            # 8 people's timesheets at once) files each employee's OWN
            # attachment(s), not the whole shared thread — see
            # isolate_employee_attachments for exactly when this applies and
            # when it safely falls back to the single-employee behaviour.
            isolated = isolate_employee_attachments(t, raw_bytes)
            if isolated:
                attachments.extend(isolated)
            else:
                import mimetypes as _mt
                fn = t.filename or "attachment"
                ct = _mt.guess_type(fn)[0] or "application/octet-stream"
                attachments.append((fn, ct, raw_bytes))
    approval = None
    if approval_status in ("approved", "not_approved"):
        approval = {"approved": approval_status == "approved",
                    "detail": (approval_detail or "").strip()}

    # Unique per-sheet source key so accepting several sheets for the same
    # employee+month (attendance sheet + a separate sick-leave certificate)
    # UNIONS their leaves instead of the later Accept overwriting the earlier.
    from app.services.pipeline.ingestion import _file_key
    source_key = _file_key(t.source_id, t.attachment_id, t.filename or t.id)
    try:
        rec, new_tracker = await ingest_manual_entry(
            db, employee_pk=employee_pk, month=month, year=year,
            buckets=bucket_data, attachments=attachments, note=note,
            approval=approval,
            source_key=source_key, source_filename=(t.filename or "sheet"),
        )
    except ValueError as e:
        raise HTTPException(400, str(e))

    # ingest_manual_entry created its own tracker — delete it; the original t
    # is the authoritative audit row for this file.
    await db.delete(new_tracker)

    # Update original tracker to reflect the manual resolution.
    t.record_id = rec.id
    t.status = PipelineStatus.SUCCESS
    t.failure_code = None
    t.failure_detail = None
    t.resolved_at = datetime.now(timezone.utc)
    t.resolution_note = (note or "").strip() or "Filed via Review."
    t.month = rec.month
    t.year = rec.year
    t.employee_name = rec.employee_name
    t.employee_id = rec.employee_id
    t.events = (t.events or []) + [{
        "stage": "recorded", "status": "ok",
        "detail": f"Filed via Review: {t.resolution_note}",
        "at": t.resolved_at.isoformat(),
    }]
    purge_raw_copy(t)
    # Accepting an email-sourced item files its record → the inbox row is now
    # "ingested" (the staged flows never go through the legacy Accept decision).
    from app.services.pipeline.ingestion import mark_source_email_ingested
    await mark_source_email_ingested(db, t)
    await db.commit()
    await db.refresh(t)
    await datacache.bust_pipeline()
    return _out(t)


@router.get("/{pipeline_id}/raw-preview")
async def pipeline_raw_preview(pipeline_id: str, db: AsyncSession = Depends(get_db)):
    """Serve the stored raw file for inline preview (PDF / image / EML download)."""
    from fastapi import Response as _Response
    t = (await db.execute(select(PipelineFile).where(PipelineFile.id == pipeline_id))).scalar_one_or_none()
    if not t:
        raise HTTPException(404, "Pipeline file not found")
    from app.services.pipeline.ingestion import read_raw_copy
    data = read_raw_copy(t)
    if not data:
        raise HTTPException(404, "Raw file copy is no longer available")
    from app.services.extraction.file_processor import content_type_for, detect_file_type
    fname = t.filename or "file"
    media = content_type_for(fname, data, t.content_type)
    ftype = detect_file_type(fname, data)
    inline = ftype in ("pdf", "image", "eml") or media.startswith(("image/", "application/pdf", "message/", "text/"))
    disp = "inline" if inline else "attachment"
    return _Response(content=data, media_type=media,
                     headers={"Content-Disposition": content_disposition(disp, fname)})


@router.get("/{pipeline_id}/raw-render")
async def pipeline_raw_render(
    pipeline_id: str,
    page: int = Query(default=1, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
):
    """Server-side render of the raw DOCX/XLSX/PDF copy to a page image —
    previews that work in every browser; the original stays downloadable."""
    from fastapi import Response as _Response
    from app.services.extraction.file_processor import detect_file_type, to_page_images
    t = (await db.execute(select(PipelineFile).where(PipelineFile.id == pipeline_id))).scalar_one_or_none()
    if not t:
        raise HTTPException(404, "Pipeline file not found")
    from app.services.pipeline.ingestion import read_raw_copy
    data = read_raw_copy(t)
    if not data:
        raise HTTPException(404, "Raw file copy is no longer available")
    ftype = detect_file_type(t.filename or "", data)
    if ftype not in ("docx", "xlsx", "pdf"):
        raise HTTPException(400, f"No server render for type '{ftype}'")
    try:
        imgs = to_page_images(ftype, data)
    except Exception:
        raise HTTPException(422, "Could not render this file")
    if not imgs:
        raise HTTPException(422, "Could not render this file")
    idx = min(page, len(imgs)) - 1
    return _Response(content=imgs[idx], media_type="image/jpeg",
                     headers={"X-Page-Count": str(len(imgs))})


@router.get("/{pipeline_id}/raw-eml-preview")
async def pipeline_raw_eml_preview(pipeline_id: str, db: AsyncSession = Depends(get_db)):
    """Parse the raw EML (or Outlook .msg) file and return structured content
    as JSON for the viewer."""
    t = (await db.execute(select(PipelineFile).where(PipelineFile.id == pipeline_id))).scalar_one_or_none()
    if not t:
        raise HTTPException(404, "Pipeline file not found")
    if not (t.filename or "").lower().endswith((".eml", ".msg")):
        raise HTTPException(400, "Not an EML or .msg file")
    from app.services.pipeline.ingestion import read_raw_copy
    data = read_raw_copy(t)
    if not data:
        raise HTTPException(404, "Raw file copy is no longer available")
    from app.services.extraction.eml_parser import parse_eml
    try:
        return parse_eml(data, filename=t.filename)
    except Exception:
        raise HTTPException(422, "Could not parse this email file")


@router.get("/meta/stages")
async def pipeline_stages():
    return {"stages": PipelineStage.ORDER, "failure_labels": FAILURE_LABELS}
