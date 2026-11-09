"""
Record filing + pipeline-tracker plumbing.

EXTRACTION lives in services/agents/full_email_extract.py — the ONE pipeline
every entry point uses (Extract Email, selected attachments, Upload page,
chat store). It stages NEEDS_REVIEW items; nothing here runs an LLM.

This module owns what happens around review:
  - ingest_manual_entry: the reviewer's Accept (Compare & Fix / manual form)
    — files the record, unions multi-file months, writes the vault.
  - retry / resolve-with-employee: re-analyse via the shared pipeline and
    re-stage, or file under a reviewer-chosen employee.
  - raw-copy storage helpers + tracker event helpers.

MULTI-FILE MONTHS: clients sending weekly / 15-day sheets produce several files
for the same employee + month. Each file's extracted buckets are stored as an
entry in TimesheetRecord.source_files and the record's buckets are the UNION of
all entries — so a second file MERGES into the month instead of being treated
as a duplicate. Re-uploading the same file replaces its own entry (idempotent).
"""
from __future__ import annotations

import calendar
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.email_message import EmailMessage, EmailStatus
from app.models.employee import Employee
from app.models.pipeline_file import FailureCode, PipelineFile, PipelineStage, PipelineStatus
from app.models.timesheet_record import ApprovalStatus, TimesheetRecord, ValidationStatus
from app.services.pipeline import raw_store
from app.services import storage_provider as sp
from app.services.extraction.validation import summarize as summarize_record
from app.services.extraction.validation import validate

BUCKET_FIELDS = {
    "annual": "annual_leave_dates",
    "remote": "remote_work_dates",
    "sick": "sick_leave_dates",
    "maternity": "maternity_leave_dates",
    "unpaid": "unpaid_leave_dates",
    "absent": "absent_dates",
    "public_holiday": "public_holiday_dates",
    "other": "other_leave_dates",
    # Not leave — day-accounting fields (worked / off) — but reviewer-editable
    # and multi-file-unioned exactly like the leave buckets above.
    "working": "working_dates",
    "weekend": "weekend_dates",
}

# Failed / flagged files a reviewer can complete by picking the right employee
# and/or providing the period. The pipeline re-runs extraction on the raw copy.
RESOLVABLE_MATCH_CODES = frozenset({
    FailureCode.AMBIGUOUS_ID,
    FailureCode.EMPLOYEE_NOT_MATCHED,
    FailureCode.ID_NAME_MISMATCH,
    FailureCode.NAME_NOT_FOUND,    # LLM found period but no identity — pick employee
    FailureCode.MONTH_NOT_FOUND,   # LLM found identity but no period — pick period
    FailureCode.PENDING_REVIEW,    # AI-extracted, staged for accept via Review
})


# ---------------------------------------------------------------------------
# Pipeline tracker helpers
# ---------------------------------------------------------------------------
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _event(t: PipelineFile, stage: str, status: str, detail: str) -> None:
    """Append an audit event and advance the stage (JSON column => reassign)."""
    t.stage = stage
    t.events = (t.events or []) + [{"stage": stage, "status": status,
                                    "detail": detail, "at": _now_iso()}]


def read_raw_copy(t: PipelineFile) -> bytes | None:
    return raw_store.read_raw(t.raw_path)


def purge_raw_copy(t: PipelineFile) -> None:
    """Delete the retry copy and forget it. Done once a file no longer needs a
    retry — i.e. it succeeded, was resolved, or its tracker row is deleted — so
    only failed / needs-review files keep an original around."""
    raw_store.delete_raw(t.raw_path)
    t.raw_path = None


def isolate_employee_attachments(t: PipelineFile, raw_bytes: bytes) -> list[tuple[str, str, bytes]] | None:
    """For an email that carried MULTIPLE employees' sheets at once (a manager
    sending 8 people's timesheets in one message), pull out ONLY this
    employee's own attachment(s) from the shared raw copy — instead of
    filing the whole multi-employee bundle into every one of their vault
    folders.

    Returns None whenever it's not safe to guess, and the caller should file
    the whole raw copy exactly as it always has:
      - this wasn't a multi-employee run (single-employee email keeps its
        existing, deliberate "file the whole .eml for provenance" behaviour
        — see test_eml_attachment_not_stored_separately_with_provenance),
      - or isolation can't find EVERY one of this employee's own sheets by
        filename in the raw copy (ambiguous/altered/not an email at all).
    Filing the full context is always safe; filing the wrong slice of
    someone else's data is not, so any doubt falls back rather than guesses.
    """
    if t.source_kind != "email":
        return None
    meta = (t.extraction_meta or {}).get("full_email_extract") or {}
    if int(meta.get("group_count") or 1) <= 1:
        return None
    wanted = [s.get("filename") for s in (meta.get("sheets") or []) if s.get("filename")]
    if not wanted:
        return None
    # Copies of the same timesheet that were stored with the item but not read
    # (e.g. the unsigned copy of a signed sheet) belong to this employee too.
    for alt in meta.get("alternates") or []:
        wanted += [n for n in (alt.get("stored_only") or []) if n]

    from app.services.extraction.file_processor import eml_all_attachments
    try:
        found = eml_all_attachments(raw_bytes)
    except Exception:
        return None
    by_name: dict[str, tuple[bytes, str]] = {}
    ambiguous: set[str] = set()
    for name, payload, ftype in found:
        if name in by_name:
            ambiguous.add(name)      # two different files share a name (e.g. a signed + an unsigned copy)
        by_name.setdefault(name, (payload, ftype))
    if any(fn in ambiguous for fn in wanted):
        return None                  # can't tell which one is this item's: file the whole email instead

    import mimetypes as _mt
    matched: list[tuple[str, str, bytes]] = []
    seen: set[str] = set()
    for fn in wanted:
        if fn in seen:
            continue
        hit = by_name.get(fn)
        if hit is None:
            return None  # any miss -> don't guess, file the whole thing instead
        payload, _ftype = hit
        ct = _mt.guess_type(fn)[0] or "application/octet-stream"
        matched.append((fn, ct, payload))
        seen.add(fn)
    return matched or None


def relocate_legacy_pipeline_raw() -> None:
    """One-time cleanup: older builds stored retry copies under
    storage/_pipeline/<id>/, which made them appear in the File Vault. Move any
    such folder into data/pipeline_raw/ so it disappears from the vault while
    keeping Retry working. Best-effort; safe to run on every startup."""
    import shutil
    legacy = settings.storage_path / "_pipeline"
    if not legacy.is_dir():
        return
    dest_root = settings.pipeline_raw_path
    for child in legacy.iterdir():
        target = dest_root / child.name
        if child.is_dir() and not target.exists():
            try:
                shutil.move(str(child), str(target))
            except Exception:
                continue
    try:
        legacy.rmdir()  # only succeeds if now empty
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Multi-file month merging
# ---------------------------------------------------------------------------
def _file_key(source_id: str | None, attachment_id: str | None, filename: str) -> str:
    return f"{source_id or 'upload'}::{attachment_id or filename}"


def _merge_source_files(existing_entries: list, new_entry: dict) -> list:
    """Replace the entry with the same key (re-upload), else append (new week)."""
    out = [e for e in (existing_entries or []) if e.get("key") != new_entry["key"]]
    out.append(new_entry)
    return out


_REPEATS_ON_EVERY_SHEET = frozenset({"public_holiday", "weekend"})


def _union_buckets(entries: list) -> tuple[dict, list[str]]:
    """Union each bucket across all contributing files; flag dates that two
    DIFFERENT files both claim (overlapping weekly sheets), except public
    holidays and weekends, which are calendar facts that legitimately repeat
    identically on every overlapping sheet rather than a claim that could
    disagree."""
    merged: dict[str, list[str]] = {b: [] for b in BUCKET_FIELDS}
    overlap_flags: list[str] = []
    for b in BUCKET_FIELDS:
        seen: dict[str, str] = {}  # date -> first filename that claimed it
        for e in entries:
            fname = e.get("filename") or "file"
            for d in (e.get("buckets", {}).get(b) or []):
                if d in seen:
                    if b not in _REPEATS_ON_EVERY_SHEET and seen[d] != fname:
                        overlap_flags.append(
                            f"Date {d} is claimed by two files ({seen[d]} and {fname}) "
                            f"in the same category."
                        )
                else:
                    seen[d] = fname
        merged[b] = sorted(seen.keys())
    return merged, list(dict.fromkeys(overlap_flags))


async def _find_existing(
    db: AsyncSession, matched_pk: str | None, employee_id: str | None,
    employee_name: str | None, month: int, year: int,
) -> TimesheetRecord | None:
    rows = (await db.execute(select(TimesheetRecord).where(
        TimesheetRecord.month == month, TimesheetRecord.year == year))).scalars().all()
    # Strongest key: the matched employee PK (safe with duplicate AUH/DXB ids).
    if matched_pk:
        for r in rows:
            if r.matched_employee_pk == matched_pk:
                return r
    name_norm = (employee_name or "").strip().lower()
    for r in rows:
        if r.matched_employee_pk and matched_pk and r.matched_employee_pk != matched_pk:
            continue
        # id alone is NOT enough (AUH/DXB share employee_id ranges) — the name
        # must also agree. A blank name (nothing usable extracted/typed) can
        # NEVER merge on id alone, even for a legacy row with no
        # matched_employee_pk yet — that would risk silently merging this
        # entry's leave into a DIFFERENT person's month record just because
        # they happen to share a raw id. Safer to fall through to "no
        # existing match" (the caller then files a new record) than guess.
        if employee_id and r.employee_id == employee_id and name_norm:
            if (r.employee_name or "").strip().lower() == name_norm:
                return r
        elif name_norm and (r.employee_name or "").strip().lower() == name_norm:
            return r
    return None


# ---------------------------------------------------------------------------
# The core unit
# ---------------------------------------------------------------------------
async def ingest_manual_entry(db: AsyncSession, **kwargs) -> tuple[TimesheetRecord, PipelineFile]:
    """Create/merge a monthly record (see _ingest_manual_entry for the details).

    Filing is serialised PER EMPLOYEE + MONTH: two Accepts that land together
    (a double click, two reviewers, a bulk accept racing a single one) would
    each look up "is there already a record?", both find none, and both create
    one - two records for the same month. Behind the lock the second Accept sees
    the first one's record and MERGES into it, exactly as intended."""
    from app.services.extract_email.thread_lock import keyed_lock

    key = f"record:lock:{kwargs.get('employee_pk')}:{kwargs.get('year')}-{kwargs.get('month')}"
    async with keyed_lock(
        key, waiting_message="Another Accept for this employee and month is in progress — waiting…",
        busy_message="Another Accept for this employee and month is still running — try again.",
    ):
        return await _ingest_manual_entry(db, **kwargs)


async def _ingest_manual_entry(
    db: AsyncSession, *, employee_pk: str, month: int, year: int,
    buckets: dict[str, list[str]],
    attachments: list[tuple[str, str, bytes]] | None = None,
    note: str | None = None,
    approval: dict | None = None,
    source_key: str | None = None,
    source_filename: str | None = None,
) -> tuple[TimesheetRecord, PipelineFile]:
    """Create/merge a monthly record from MANUALLY entered leave data (no LLM),
    optionally with attached files. Runs the SAME vault filing + validation +
    pipeline-tracker flow as upload/email. The employee MUST be picked from the
    matcher (strict identity — we never guess).

    `approval` is the reviewer's explicit manager-approval verdict from
    Compare & Fix: {"approved": bool, "detail": str} — it overrides whatever
    the record currently says. None keeps the existing behaviour."""
    attachments = attachments or []
    if not (1 <= month <= 12 and year >= 2000):
        raise ValueError("Invalid month or year.")
    matched = (await db.execute(select(Employee).where(Employee.id == employee_pk))).scalar_one_or_none()
    if not matched:
        raise ValueError("Selected employee was not found in the matcher list.")
    # Fresh read so ACO/DCO added after the row was first loaded in this
    # session (or via import) always land in the vault folder name.
    await db.refresh(matched)

    employee_name = matched.name
    account_manager = matched.account_manager

    tracker = PipelineFile(
        filename=(attachments[0][0] if attachments else "Manual entry"),
        content_type=(attachments[0][1] if attachments else "application/json"),
        size_bytes=sum(len(a[2]) for a in attachments),
        source_kind="manual", source_id=f"manual:{matched.employee_id}:{month}-{year}",
    )
    db.add(tracker)
    await db.flush()
    _event(tracker, PipelineStage.RECEIVED, "ok",
           f"Manual entry for {employee_name} — {calendar.month_name[month]} {year} "
           f"({len(attachments)} attachment(s)).")
    tracker.employee_name = employee_name
    tracker.employee_id = matched.employee_id
    tracker.month, tracker.year = month, year
    tracker.extraction_model = None
    tracker.extraction_method = "manual"
    tracker.used_ocr = False
    tracker.extraction_meta = {"method": "manual", "model": None, "used_ocr": False,
                               "source_kind": "manual"}
    _event(tracker, PipelineStage.MATCHING, "ok",
           f"Manually assigned to {employee_name} ({matched.employee_id}).")

    # Each distinct source sheet gets its OWN key so accepting a SECOND sheet
    # for the same employee+month (e.g. the attendance sheet, then a separate
    # sick-leave certificate) UNIONS with the first instead of replacing it.
    # Re-accepting the SAME sheet reuses its key → its contribution is replaced,
    # not doubled. A bare manual-form entry (no source) keeps the legacy single
    # "manual_entry" key so re-submitting corrects the prior manual entry.
    entry = {
        "key": source_key or "manual_entry",
        "filename": source_filename or "Manual entry",
        "source_id": tracker.source_id, "attachment_id": None, "ingested_at": _now_iso(),
        "buckets": {b: list(buckets.get(b, []) or []) for b in BUCKET_FIELDS},
    }
    existing = await _find_existing(db, matched.id, matched.employee_id, employee_name, month, year)
    # A late file joining an already-filed month (e.g. a sick-leave certificate
    # arriving after this employee's manager was corrected) must land next to
    # what's already vaulted for that month, not split off into a new manager's
    # folder — so it keeps whichever manager the month was originally filed
    # under. A brand-new month still picks up the employee's current manager.
    if existing:
        account_manager = existing.account_manager
    prior = (existing.source_files if existing else []) or []
    entries = _merge_source_files(prior, entry)
    merged, overlap_flags = _union_buckets(entries)
    cleaned, flags = validate(merged, month, year)
    flags = list(dict.fromkeys(overlap_flags + flags))
    validation_status = ValidationStatus.MANUAL_REVIEW if flags else ValidationStatus.VERIFIED
    _event(tracker, PipelineStage.VALIDATION, "warn" if flags else "ok",
           ("Validation raised: " + " ".join(flags[:4])) if flags else "Validation clean.")
    summary = summarize_record(cleaned, flags, month, year, len(entries))

    folder_rel = None
    storage_warn = None
    try:
        # Folder name carries the person's ACO/DCO numbers, and migrates an
        # older folder of theirs if those numbers were added/corrected later.
        emp_folder = sp.ensure_employee_folder(
            account_manager, employee_name, matched.aco_number, matched.dco_number)
        for (fn, _ct, dat) in attachments:
            # Store exactly what was handed in — the .eml (or the uploaded
            # sheet) as one file. Nested attachments/inline images inside an
            # .eml are NOT filed separately: they already live inside the
            # .eml itself, and filing them again duplicated real sheets and
            # littered the vault with signature logos/banners.
            sp.save_file(account_manager, emp_folder, month, year, fn, dat)
        folder_rel = sp.folder_rel(account_manager, emp_folder, month, year)
        _event(tracker, PipelineStage.FILING, "ok", f"Filed under {folder_rel}.")
    except Exception as e:
        storage_warn = f"Could not file on disk: {str(e)[:200]}"
        _event(tracker, PipelineStage.FILING, "warn", storage_warn)

    rec = existing or TimesheetRecord()
    rec.extracted_employee_id = matched.employee_id
    rec.extracted_employee_name = matched.name
    rec.matched_employee_pk = matched.id
    rec.employee_id = matched.employee_id
    rec.employee_name = employee_name
    rec.account_manager = account_manager
    rec.dco_number = matched.dco_number
    rec.match_note = (note or "").strip() or "Manual entry."
    rec.month, rec.year = month, year
    rec.calendar_days = calendar.monthrange(year, month)[1]
    for bucket, field in BUCKET_FIELDS.items():
        setattr(rec, field, cleaned[bucket])
    rec.source_files = entries
    rec.validation_status = validation_status
    rec.llm_summary = summary
    rec.hr_flags = flags
    if approval is not None:
        approved = bool(approval.get("approved"))
        rec.approval_status = ApprovalStatus.APPROVED if approved else ApprovalStatus.NOT_APPROVED
        rec.approval_detected = approved
        rec.approval_detail = (str(approval.get("detail") or "").strip()
                               or ("Marked approved by the reviewer in Compare & Fix."
                                   if approved else
                                   "Marked NOT approved by the reviewer in Compare & Fix."))
    elif not existing:
        rec.approval_detected = False
        rec.approval_detail = "Manually entered — pending manager sign-off."
        rec.approval_status = ApprovalStatus.PENDING
    rec.source_email_id = tracker.source_id
    if folder_rel:
        rec.storage_folder = folder_rel
    if not existing:
        db.add(rec)
    await db.flush()

    tracker.record_id = rec.id
    if storage_warn:
        tracker.status = PipelineStatus.NEEDS_REVIEW
        tracker.failure_code = FailureCode.STORAGE_ERROR
        tracker.failure_detail = storage_warn
    elif validation_status == ValidationStatus.MANUAL_REVIEW:
        tracker.status = PipelineStatus.NEEDS_REVIEW
        tracker.failure_code = FailureCode.VALIDATION_MISMATCH
        tracker.failure_detail = "; ".join(flags[:6])
    else:
        tracker.status = PipelineStatus.SUCCESS
    _event(tracker, PipelineStage.RECORDED,
           "ok" if tracker.status == PipelineStatus.SUCCESS else "warn",
           f"Record {'updated' if existing else 'created'} for {employee_name} — "
           f"{calendar.month_name[month]} {year}.")
    await db.commit()
    await db.refresh(rec)
    await db.refresh(tracker)
    return rec, tracker


async def mark_source_email_ingested(db: AsyncSession, tracker: PipelineFile) -> None:
    """A record filed from an email-sourced pipeline item means that email has
    been ingested — reflect it on the inbox row so the New/Ingested filter is
    truthful. The staged flows (Extract Email, Run Extraction) file records via
    the pipeline, never via the legacy Accept decision that used to set this."""
    if (tracker.source_kind or "") != "email" or not tracker.source_id:
        return
    row = (await db.execute(select(EmailMessage).where(
        EmailMessage.provider_message_id == tracker.source_id))).scalar_one_or_none()
    if row is not None and row.status != EmailStatus.INGESTED:
        row.status = EmailStatus.INGESTED
        row.decided_at = datetime.now(timezone.utc)


def can_resolve_assign(tracker: PipelineFile) -> bool:
    if tracker.status not in (PipelineStatus.FAILED, PipelineStatus.NEEDS_REVIEW):
        return False
    if tracker.failure_code not in RESOLVABLE_MATCH_CODES:
        return False
    if tracker.raw_path:
        return True
    return bool(tracker.source_kind == "email" and tracker.source_id and tracker.attachment_id)
