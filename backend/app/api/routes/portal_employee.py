"""
Portal employee routes — self-service submission of a month's documents:
a timesheet, a sick-leave certificate, and/or another document. None of the
three slots is individually mandatory — any one file is a complete
submission (e.g. a sick-leave certificate filed on its own, with no
timesheet); only having zero files at all blocks Submit.

  GET    /portal/employee/submissions/{month}/{year}   precheck for a period
  GET    /portal/employee/submissions                   history list
  POST   /portal/employee/submissions                    create/update a draft
  POST   /portal/employee/submissions/{id}/files          upload/replace one slot
  DELETE /portal/employee/submissions/{id}/files/{kind}   remove one slot
  GET    /portal/employee/submissions/{id}/files/{kind}/content  preview/download
  GET    /portal/employee/submissions/{id}/files/{kind}/render   server-render (DOCX etc.)
  POST   /portal/employee/submissions/{id}/submit         first send-in
  DELETE /portal/employee/submissions/{id}                discard an abandoned draft

Files are editable at ANY submission status, not just draft — a certificate
that arrives days after the timesheet itself is the normal case. Adding or
removing a file on a submission that was already sent back in front of the
manager for a fresh decision and re-runs extraction automatically (see
_requeue_for_review); /submit itself is only needed for the FIRST send (or a
plain resend after a rejection with no file changes).

Extraction runs in the BACKGROUND (see services/extract_email/portal_extract.py)
— it does not wait for, or depend on, the employee's manager. Everything
here only ever touches the CALLING employee's own submissions; every route
resolves the submission by id AND `employee_pk == user.employee_pk`, so one
employee can never see or edit another's rows (returns 404, not 403, so a
guessed id can't even confirm another submission exists).
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.portal_deps import require_portal_employee
from app.core.database import get_db
from app.models.employee import Employee
from app.models.portal_auth import PortalUser
from app.models.portal_submission import (
    ExtractionState,
    ManagerDecision,
    PortalSubmission,
    PortalSubmissionFile,
    PortalSubmissionStatus,
    SubmissionFileKind,
)
from app.models.timesheet_record import TimesheetRecord
from app.schemas.portal import (
    PortalSubmissionFileOut,
    PortalSubmissionOut,
    PortalSubmissionPrecheckOut,
    PortalSubmissionUpsertIn,
    PortalSubmitIn,
)
from app.services.pipeline import portal_store

router = APIRouter(prefix="/portal/employee", tags=["portal"])


async def _file_out(f: PortalSubmissionFile) -> PortalSubmissionFileOut:
    return PortalSubmissionFileOut(id=f.id, kind=f.kind, filename=f.filename,
                                    content_type=f.content_type, size_bytes=f.size_bytes,
                                    created_at=f.created_at)


async def _review_state(db: AsyncSession, sub: PortalSubmission) -> tuple[str, str | None]:
    """Computed on read, same as the internal admin roster's version — never
    written back by Accept, so this is the ONLY place "has this been
    accepted yet" gets resolved for the employee's own view too."""
    from app.models.pipeline_file import PipelineFile, PipelineStatus

    linked = (await db.execute(select(PipelineFile).where(
        PipelineFile.source_kind == "portal",
        PipelineFile.source_id.like(f"portal:{sub.id}:%"),
    ))).scalars().all()
    record_id = next((f.record_id for f in linked if f.record_id), None)
    if linked:
        statuses = {f.status for f in linked}
        review_state = ("accepted" if record_id and all(f.record_id for f in linked)
                        else "needs_review" if PipelineStatus.NEEDS_REVIEW in statuses
                        else "success" if statuses == {PipelineStatus.SUCCESS}
                        else "processing")
    else:
        review_state = "pending" if sub.extraction_state != "done" else "no_files_staged"
    return review_state, record_id


async def _submission_out(db: AsyncSession, sub: PortalSubmission, emp: Employee | None) -> PortalSubmissionOut:
    files = (await db.execute(select(PortalSubmissionFile).where(
        PortalSubmissionFile.submission_id == sub.id))).scalars().all()
    review_state, record_id = await _review_state(db, sub)
    return PortalSubmissionOut(
        id=sub.id, employee_pk=sub.employee_pk,
        employee_name=emp.name if emp else None, employee_id=emp.employee_id if emp else None,
        month=sub.month, year=sub.year, status=sub.status,
        approval_claimed=sub.approval_claimed,
        manager_decision=sub.manager_decision, manager_note=sub.manager_note,
        decided_at=sub.decided_at, employee_note=sub.employee_note,
        extraction_state=sub.extraction_state, extraction_error=sub.extraction_error,
        review_state=review_state, record_id=record_id,
        submitted_at=sub.submitted_at, created_at=sub.created_at, updated_at=sub.updated_at,
        files=[await _file_out(f) for f in files],
    )


async def _own_submission(db: AsyncSession, user: PortalUser, submission_id: str) -> PortalSubmission:
    sub = (await db.execute(select(PortalSubmission).where(
        PortalSubmission.id == submission_id))).scalar_one_or_none()
    if not sub or sub.employee_pk != user.employee_pk:
        raise HTTPException(404, "Submission not found")
    return sub


async def _mark_pending_review(db: AsyncSession, sub: PortalSubmission) -> None:
    """Put an already-submitted item back in front of a reviewer as pending —
    used whenever the file set changes, whether or not that change actually
    needs new extraction (e.g. a pure removal with files left over doesn't:
    nothing about the REMAINING files changed, so there's nothing new to
    read; see delete_file). Never touches extraction_state — only a caller
    that's about to actually dispatch a new run does that."""
    sub.status = PortalSubmissionStatus.SUBMITTED
    sub.manager_decision = ManagerDecision.PENDING
    sub.manager_note = None
    sub.decided_at = None
    sub.decided_by = None
    sub.submitted_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(sub)


async def _requeue_for_review(db: AsyncSession, sub: PortalSubmission, only_kind: str | None = None) -> None:
    """A file was added or replaced on a submission that was already sent
    in — e.g. a sick-leave certificate arriving days after the timesheet
    itself. New evidence means a fresh look (_mark_pending_review), and
    extraction re-runs — but ONLY on the one slot that actually just
    changed (only_kind), never the whole file set: a file that didn't
    change has nothing new to read, and re-running it anyway would waste an
    LLM call and needlessly reopen/refresh an item a reviewer may already be
    looking at. only_kind=None is for the very first Submit only, where
    every currently-present file is genuinely new to extraction — see
    submit() below."""
    await _mark_pending_review(db, sub)
    sub.extraction_state = ExtractionState.NOT_STARTED
    sub.extraction_error = None
    await db.commit()
    await db.refresh(sub)
    from app.services.tasks import run_portal_extraction_task
    run_portal_extraction_task.delay(sub.id, only_kind)
    await db.refresh(sub)


async def _withdraw_unaccepted_pipeline_file(db: AsyncSession, submission_id: str, kind: str) -> None:
    """A slot's file was removed — withdraw its staged PipelineFile too, so a
    reviewer never sees a Compare & Fix item for a file that no longer
    exists. Never touches one that already filed a record (record_id set) —
    same "don't erase a live audit trail" rule stage_groups itself follows."""
    from app.models.pipeline_file import PipelineFile
    from app.services.pipeline import raw_store

    pf = (await db.execute(select(PipelineFile).where(
        PipelineFile.source_kind == "portal",
        PipelineFile.source_id == f"portal:{submission_id}:{kind}",
    ))).scalar_one_or_none()
    if pf and not pf.record_id:
        raw_store.delete_raw(pf.raw_path)
        await db.delete(pf)


@router.get("/submissions/{month}/{year}", response_model=PortalSubmissionPrecheckOut)
async def precheck(month: int, year: int, user: PortalUser = Depends(require_portal_employee),
                    db: AsyncSession = Depends(get_db)):
    if not (1 <= month <= 12 and year >= 2000):
        raise HTTPException(400, "Invalid month or year.")
    emp = (await db.execute(select(Employee).where(Employee.id == user.employee_pk))).scalar_one_or_none()
    sub = (await db.execute(select(PortalSubmission).where(
        PortalSubmission.employee_pk == user.employee_pk,
        PortalSubmission.month == month, PortalSubmission.year == year,
    ))).scalar_one_or_none()
    existing_record = (await db.execute(select(TimesheetRecord).where(
        TimesheetRecord.matched_employee_pk == user.employee_pk,
        TimesheetRecord.month == month, TimesheetRecord.year == year,
    ))).scalar_one_or_none()
    note = None
    if existing_record:
        note = (f"{month:02d}/{year} already has a filed record"
                f"{f' (from {existing_record.source_email_id})' if existing_record.source_email_id else ''}"
                f" — anything you submit will be added to it, not replace it.")
    return PortalSubmissionPrecheckOut(
        submission=await _submission_out(db, sub, emp) if sub else None,
        already_filed_elsewhere=bool(existing_record), filed_source_note=note,
    )


@router.get("/submissions", response_model=list[PortalSubmissionOut])
async def list_submissions(user: PortalUser = Depends(require_portal_employee),
                            db: AsyncSession = Depends(get_db)):
    emp = (await db.execute(select(Employee).where(Employee.id == user.employee_pk))).scalar_one_or_none()
    rows = (await db.execute(select(PortalSubmission).where(
        PortalSubmission.employee_pk == user.employee_pk,
    ).order_by(PortalSubmission.year.desc(), PortalSubmission.month.desc()))).scalars().all()
    return [await _submission_out(db, s, emp) for s in rows]


@router.post("/submissions", response_model=PortalSubmissionOut, status_code=201)
async def upsert_submission(body: PortalSubmissionUpsertIn, user: PortalUser = Depends(require_portal_employee),
                             db: AsyncSession = Depends(get_db)):
    if not (1 <= body.month <= 12 and body.year >= 2000):
        raise HTTPException(400, "Invalid month or year.")
    emp = (await db.execute(select(Employee).where(Employee.id == user.employee_pk))).scalar_one_or_none()
    if not emp:
        raise HTTPException(400, "Your employee record could not be found in the matcher.")
    sub = (await db.execute(select(PortalSubmission).where(
        PortalSubmission.employee_pk == user.employee_pk,
        PortalSubmission.month == body.month, PortalSubmission.year == body.year,
    ))).scalar_one_or_none()
    # No status gate here — this only ever touches the note, never files or
    # extraction, so it's always safe regardless of where the submission is.
    if not sub:
        sub = PortalSubmission(employee_pk=user.employee_pk, month=body.month, year=body.year)
        db.add(sub)
    if body.employee_note is not None:
        sub.employee_note = body.employee_note
    await db.commit()
    await db.refresh(sub)
    return await _submission_out(db, sub, emp)


@router.post("/submissions/{submission_id}/files", response_model=PortalSubmissionOut)
async def upload_file(submission_id: str, kind: str, file: UploadFile = File(...),
                       user: PortalUser = Depends(require_portal_employee), db: AsyncSession = Depends(get_db)):
    """Always allowed, at any submission status — a certificate that arrives
    days after the timesheet itself is the normal case, not an edge case.
    Adding a file to a submission that was already sent in puts it back in
    front of the reviewer (fresh look) and re-runs extraction on JUST this
    slot (only_kind=kind) — the other, unchanged slots are left exactly as
    they already were, not silently reprocessed; adding to a still-draft
    submission just saves it, same as before Submit exists."""
    if kind not in SubmissionFileKind.ALL:
        raise HTTPException(400, f"kind must be one of {SubmissionFileKind.ALL}")
    sub = await _own_submission(db, user, submission_id)
    data = await file.read()
    if not data:
        raise HTTPException(400, "The uploaded file was empty.")

    existing = (await db.execute(select(PortalSubmissionFile).where(
        PortalSubmissionFile.submission_id == sub.id, PortalSubmissionFile.kind == kind,
    ))).scalar_one_or_none()
    if existing:
        portal_store.delete_portal_file(existing.stored_path)

    stored_path = portal_store.save_portal_file(sub.id, kind, file.filename or "file", data)
    if existing:
        existing.filename = file.filename or "file"
        existing.content_type = file.content_type
        existing.stored_path = stored_path
        existing.size_bytes = len(data)
    else:
        db.add(PortalSubmissionFile(submission_id=sub.id, kind=kind, filename=file.filename or "file",
                                     content_type=file.content_type, stored_path=stored_path, size_bytes=len(data)))
    await db.commit()

    if sub.status != PortalSubmissionStatus.DRAFT:
        await _requeue_for_review(db, sub, only_kind=kind)

    emp = (await db.execute(select(Employee).where(Employee.id == user.employee_pk))).scalar_one_or_none()
    return await _submission_out(db, sub, emp)


@router.delete("/submissions/{submission_id}/files/{kind}", response_model=PortalSubmissionOut)
async def delete_file(submission_id: str, kind: str, user: PortalUser = Depends(require_portal_employee),
                       db: AsyncSession = Depends(get_db)):
    """Always allowed too — remove a file you attached by mistake, or want
    to replace, whether or not this submission has already been sent in."""
    sub = await _own_submission(db, user, submission_id)
    f = (await db.execute(select(PortalSubmissionFile).where(
        PortalSubmissionFile.submission_id == sub.id, PortalSubmissionFile.kind == kind,
    ))).scalar_one_or_none()
    if f:
        portal_store.delete_portal_file(f.stored_path)
        await db.delete(f)
        await db.commit()
        await _withdraw_unaccepted_pipeline_file(db, sub.id, kind)
        await db.commit()

    if sub.status != PortalSubmissionStatus.DRAFT:
        still_has_a_file = (await db.execute(select(PortalSubmissionFile.id).where(
            PortalSubmissionFile.submission_id == sub.id,
        ))).first()
        if still_has_a_file:
            # A pure removal — nothing about the REMAINING files changed, so
            # there's nothing new to extract. Just put it back in front of a
            # reviewer as pending (the file set changed, worth a fresh look)
            # without dispatching a wasted re-run of already-valid slots.
            await _mark_pending_review(db, sub)
        else:
            # Nothing left at all — nothing left worth a reviewer's look.
            # Back to draft; Submit will be needed again.
            sub.status = PortalSubmissionStatus.DRAFT
            sub.manager_decision = ManagerDecision.PENDING
            sub.manager_note = None
            sub.decided_at = None
            sub.decided_by = None
            sub.extraction_state = ExtractionState.NOT_STARTED
            sub.extraction_error = None
            await db.commit()
            await db.refresh(sub)

    emp = (await db.execute(select(Employee).where(Employee.id == user.employee_pk))).scalar_one_or_none()
    return await _submission_out(db, sub, emp)


@router.delete("/submissions/{submission_id}")
async def discard_draft(submission_id: str, user: PortalUser = Depends(require_portal_employee),
                         db: AsyncSession = Depends(get_db)):
    """Discard an abandoned draft entirely — never a submitted one (send it
    back through the manager or just replace its files instead)."""
    sub = await _own_submission(db, user, submission_id)
    if sub.status != PortalSubmissionStatus.DRAFT:
        raise HTTPException(409, "Only a draft (never submitted) can be discarded.")
    files = (await db.execute(select(PortalSubmissionFile).where(
        PortalSubmissionFile.submission_id == sub.id))).scalars().all()
    for f in files:
        portal_store.delete_portal_file(f.stored_path)
        await db.delete(f)
    await db.delete(sub)
    await db.commit()
    return {"deleted": submission_id}


@router.get("/submissions/{submission_id}/files/{kind}/content")
async def preview_file(submission_id: str, kind: str, user: PortalUser = Depends(require_portal_employee),
                        db: AsyncSession = Depends(get_db)):
    sub = await _own_submission(db, user, submission_id)
    f = (await db.execute(select(PortalSubmissionFile).where(
        PortalSubmissionFile.submission_id == sub.id, PortalSubmissionFile.kind == kind,
    ))).scalar_one_or_none()
    if not f:
        raise HTTPException(404, "File not found")
    data = portal_store.read_portal_file(f.stored_path)
    if not data:
        raise HTTPException(404, "File is no longer available")
    return portal_store.content_response(f.filename, f.content_type, data)


@router.get("/submissions/{submission_id}/files/{kind}/render")
async def render_file(submission_id: str, kind: str, page: int = Query(default=1, ge=1, le=50),
                       user: PortalUser = Depends(require_portal_employee), db: AsyncSession = Depends(get_db)):
    sub = await _own_submission(db, user, submission_id)
    f = (await db.execute(select(PortalSubmissionFile).where(
        PortalSubmissionFile.submission_id == sub.id, PortalSubmissionFile.kind == kind,
    ))).scalar_one_or_none()
    if not f:
        raise HTTPException(404, "File not found")
    data = portal_store.read_portal_file(f.stored_path)
    if not data:
        raise HTTPException(404, "File is no longer available")
    return portal_store.render_response(f.filename, data, page)


@router.post("/submissions/{submission_id}/submit", response_model=PortalSubmissionOut)
async def submit(submission_id: str, body: PortalSubmitIn, user: PortalUser = Depends(require_portal_employee),
                  db: AsyncSession = Depends(get_db)):
    """The FIRST send only — from draft, or resubmitting as-is after a
    send-back with no file changes. Once a submission has actually been
    submitted, adding/removing a file already re-queues it automatically
    (see upload_file/delete_file + _requeue_for_review) — there's no
    separate "submit again" step needed for that case.

    body.approval_claimed is the employee's mandatory yes/no answer to "does
    this timesheet already carry manager approval evidence?" — required by
    the schema (no default), so a client that skips asking gets a 422, not a
    silent default. It feeds Compare & Fix's "Manager approval" toggle the
    same way an AI-detected signature does for email/upload."""
    sub = await _own_submission(db, user, submission_id)
    if sub.status not in (PortalSubmissionStatus.DRAFT, PortalSubmissionStatus.REJECTED):
        raise HTTPException(409, "This submission has already been sent.")
    # A timesheet is the common case but not mandatory — a sick-leave
    # certificate or other document filed on its own is a real, complete
    # submission too. Any one file of any kind is enough to submit.
    has_any_file = (await db.execute(select(PortalSubmissionFile.id).where(
        PortalSubmissionFile.submission_id == sub.id,
    ))).first()
    if not has_any_file:
        raise HTTPException(400, "Attach at least one file before submitting.")

    sub.approval_claimed = body.approval_claimed

    # Extraction runs in the BACKGROUND from here — independent of whatever
    # the manager later decides (see portal_extract.py's module docstring).
    # In production this dispatches to a separate worker and returns
    # instantly, so the response below still shows "not_started" — that's
    # the intended fast-response behavior. Only in CELERY_TASK_ALWAYS_EAGER
    # (dev/tests) does .delay() run inline on ITS OWN db session, so `sub`
    # (this route's own in-memory copy) needs a refresh to pick up what that
    # session actually committed, rather than silently returning stale data.
    await _requeue_for_review(db, sub)

    emp = (await db.execute(select(Employee).where(Employee.id == user.employee_pk))).scalar_one_or_none()
    return await _submission_out(db, sub, emp)
