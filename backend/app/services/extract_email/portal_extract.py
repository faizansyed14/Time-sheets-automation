"""
Portal extraction — the trimmed pass-2 read for a self-service submission.

Unlike Inbox/Upload, the employee's identity, the reporting period, and each
file's kind are already KNOWN FACTS here (the employee who logged in, the
month/year they picked, the upload slot they used) — never a guess. So this
module:
  - never calls pass 1 (triage_prompt.py) at all — there is nothing to triage;
  - reuses thread_prompt.py's PASS2_SYSTEM / PASS2_USER_RULES / PASS2_OUTPUT /
    pass2_blocks / run_pass2_batch completely UNCHANGED, with no portal-only
    prompt variant. PASS2_SYSTEM's own wording ("each item... has already
    been confirmed... you have been told whose it is meant to be") already
    fits a portal submission exactly as well as a pass-1-confirmed email
    sheet — there is no new prompt text to write or maintain here;
  - bypasses grouping.py's group_sheets()/its fuzzy identity matcher (that
    exists to resolve a GUESSED identity against the HR master — irrelevant
    when identity is a login fact), reusing only grouping.normalise_sheet()
    and grouping.tag_for();
  - stages every file via staging.stage_groups() — the SAME single staging
    path Inbox/Upload use — so Compare & Fix, Accept and ingest_manual_entry
    need zero portal-specific code.

Runs immediately after Submit (see api/routes/portal_employee.py's submit
route + services/tasks.py's run_portal_extraction_task) — NOT gated on the
manager's decision, which is recorded independently on PortalSubmission and
never read by this function.

Runs over EVERY currently-present file only on the first Submit; every later
trigger (a new/replaced file on an already-submitted item) passes
only_kind so just that one changed slot gets (re-)read — see
run_portal_extract's own docstring below.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.employee import Employee
from app.models.month_calendar import MonthCalendar
from app.models.portal_submission import (
    ExtractionState,
    PortalSubmission,
    PortalSubmissionFile,
    SubmissionFileKind,
)
from app.services.extract_email.constants import BUCKETS
from app.services.extract_email.grouping import normalise_sheet, tag_for
from app.services.extract_email.staging import stage_groups
from app.services.extract_email.thread_extract import (
    collect_thread,
    require_vision_configured,
    resolve_source,
)
from app.services.extract_email.thread_prompt import run_pass2_batch
from app.services.extract_email.upload import as_thread_messages
from app.services.pipeline import portal_store

_KIND_TO_PASS2 = {
    SubmissionFileKind.TIMESHEET: "timesheet",
    SubmissionFileKind.SICK_LEAVE: "leave_certificate",
    SubmissionFileKind.OTHER: "leave_certificate",
}


async def run_portal_extract(db: AsyncSession, submission: PortalSubmission, only_kind: str | None = None) -> None:
    """only_kind restricts this run to ONE slot — the file that actually
    just changed (a new upload, or a replacement) — instead of every file on
    the submission. A file that didn't change has nothing new to read, and
    re-running it anyway wastes an LLM call and needlessly refreshes/reopens
    an already-staged, already-reviewed-or-being-reviewed item for no
    reason. First submit is the one case that legitimately covers every
    currently-present file at once (nothing has been staged yet), so callers
    pass only_kind=None only there — see api/routes/portal_employee.py."""
    submission.extraction_state = ExtractionState.RUNNING
    await db.commit()
    try:
        matched = (await db.execute(select(Employee).where(
            Employee.id == submission.employee_pk))).scalar_one_or_none()
        if not matched:
            raise ValueError("Employee not found in the matcher.")
        files_q = select(PortalSubmissionFile).where(PortalSubmissionFile.submission_id == submission.id)
        if only_kind:
            files_q = files_q.where(PortalSubmissionFile.kind == only_kind)
        files = (await db.execute(files_q)).scalars().all()
        if not files:
            raise ValueError("No files to extract.")

        api_key, model = require_vision_configured()

        cal_row_obj = (await db.execute(select(MonthCalendar).where(
            MonthCalendar.month == submission.month, MonthCalendar.year == submission.year,
        ))).scalar_one_or_none()
        cal_row = ({"weekend_weekdays": cal_row_obj.weekend_weekdays or [],
                    "public_holidays": cal_row_obj.public_holidays or []}
                   if cal_row_obj else None)
        calendars = {(submission.month, submission.year): cal_row} if cal_row else {}

        # ---- one Item per file, all in ONE combined Thread so keys (A1, A2,
        # ...) are unique across every file in this submission — three
        # separate collect_thread() calls would each start renumbering at A1,
        # making a shared pass-2 batch ambiguous. msg_index maps each Item
        # straight back to the file that produced it.
        originals: dict[str, bytes] = {}
        messages: list[tuple[str, bytes]] = []
        for f in files:
            data = portal_store.read_portal_file(f.stored_path)
            if not data:
                continue
            originals[f.id] = data
            messages.extend(as_thread_messages(f.filename, data))
        if not messages:
            raise ValueError("None of the submitted files could be read from storage.")

        th = collect_thread(messages)
        item_by_msg_index = {it.msg_index: it for it in th.items}

        period_hint = f"{submission.month:02d}/{submission.year}"
        pairs = []
        file_by_item_key: dict[str, PortalSubmissionFile] = {}
        for i, f in enumerate([f for f in files if f.id in originals]):
            item = item_by_msg_index.get(i)
            if item is None:
                continue
            file_by_item_key[item.key] = f
            pairs.append((item, {
                "kind": _KIND_TO_PASS2.get(f.kind, "leave_certificate"),
                "employee_name": matched.name, "employee_id": matched.employee_id,
                "period_hint": period_hint,
            }))
        if not pairs:
            raise ValueError("None of the submitted files could be read.")

        # Batch by image count — identical bound/logic to thread_extract.py's
        # own pass-2 loop, just over this submission's own pairs.
        batches: list[list] = []
        cur: list = []
        n = 0
        for pair in pairs:
            k = max(len(pair[0].images), 1)
            if cur and n + k > settings.max_images_per_call:
                batches.append(cur)
                cur, n = [], 0
            cur.append(pair)
            n += k
        if cur:
            batches.append(cur)

        raw_sheets: list[dict] = []
        for bi, batch in enumerate(batches, 1):
            data = await run_pass2_batch(
                batch, "", model, api_key, calendars=calendars,
                label=f"portal-{submission.id}-batch{bi}")
            raw_sheets += data.get("sheets") or []

        for item_key, f in file_by_item_key.items():
            item = next(it for it in th.items if it.key == item_key)
            raw = next((s for s in raw_sheets if resolve_source(s.get("source"), [item]) is item), None)
            if raw is None:
                continue
            kind = _KIND_TO_PASS2.get(f.kind, "leave_certificate")
            sheet = {
                "name": item.name, "kind": kind,
                "employee_name": matched.name, "employee_id": matched.employee_id,
                "month": submission.month, "year": submission.year,
                "days_covered": raw.get("days_covered") or 0,
                "period_type": raw.get("period_type") or "unknown",
                "missing_days": raw.get("missing_days") or [],
                "working_days": raw.get("working_days") or [],
                "weekend_days": raw.get("weekend_days") or [],
                "uncertain_days": raw.get("uncertain_days") or [],
                "annual": raw.get("annual") or [], "remote": raw.get("remote") or [],
                "sick": raw.get("sick") or [], "maternity": raw.get("maternity") or [],
                "unpaid": raw.get("unpaid") or [], "absent": raw.get("absent") or [],
                "public_holiday": raw.get("public_holiday") or [], "other": raw.get("other") or [],
                "manager_signature": False, "approval_evidence": "", "approval_named_only": False,
                "text": item.text or "", "notes": str(raw.get("notes") or ""),
            }
            ns = normalise_sheet(sheet, cal_row)
            group = {
                "tag": tag_for(f"portal:{submission.id}:{f.kind}", submission.month, submission.year),
                "employee_pk": matched.id, "name": matched.name, "employee_id": matched.employee_id,
                "note": ("Portal submission — identity and period confirmed by the "
                         "employee's own login, not read from the document."),
                "month": submission.month, "year": submission.year,
                "sheets": [ns],
                "working_days": ns["working_days"], "weekend_days": ns["weekend_days"],
                "buckets": {b: ns[b] for b in BUCKETS},
                "uncertain_days": ns["uncertain_days"], "issues": ns["_issues"],
                "missing_days": ns["missing_days"], "unaccounted_days": ns["_unaccounted_days"],
                "days_covered_total": ns.get("days_covered") or 0,
                "period_types": [ns["period_type"]] if ns.get("period_type") else [],
                "overlap_flags": [], "fold_notes": [],
            }
            # The employee's own mandatory yes/no answer at Submit — the
            # SAME "Manager approval" toggle every other source populates
            # (AI-detected signature for email/upload), just a
            # self-attestation here instead of a model's read of the page.
            # The internal reviewer sees this pre-filled in Compare & Fix
            # and can still override it either way before Accepting.
            approval = {
                "detected": bool(submission.approval_claimed),
                "detail": ("Employee confirmed manager approval is on this timesheet."
                           if submission.approval_claimed else
                           "Employee indicated manager approval is NOT yet on this timesheet."),
            }
            await stage_groups(
                db, source_kind="portal", source_id=f"portal:{submission.id}:{f.kind}",
                thread_key=f"portal:{submission.id}:{f.kind}",
                raw_bytes=originals[f.id], raw_name=f.filename,
                content_type=f.content_type or "application/octet-stream",
                groups=[group], approval=approval,
                run_meta={"method": "portal-pass2", "model": model, "calls": len(batches)})

        submission.extraction_state = ExtractionState.DONE
        submission.extraction_error = None
    except Exception as e:
        submission.extraction_state = ExtractionState.FAILED
        submission.extraction_error = str(e)[:2000]
    await db.commit()
