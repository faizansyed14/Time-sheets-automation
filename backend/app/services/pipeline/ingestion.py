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
from app.services.email_provider import get_email_provider
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


def _fail(t: PipelineFile, stage: str, code: str, detail: str) -> None:
    _event(t, stage, "fail", detail)
    t.status = PipelineStatus.FAILED
    t.failure_code = code
    t.failure_detail = detail


def _save_raw_copy(t: PipelineFile, filename: str, data: bytes) -> None:
    """Keep the original bytes (OUTSIDE the File Vault) so Retry works. Stored in
    S3 under settings.s3_raw_prefix when STORAGE_PROVIDER=s3, else on local disk
    under data/pipeline_raw/<id>/ — see services/pipeline/raw_store.py."""
    t.raw_path = raw_store.save_raw(t.id, filename, data)


def read_raw_copy(t: PipelineFile) -> bytes | None:
    return raw_store.read_raw(t.raw_path)


def purge_raw_copy(t: PipelineFile) -> None:
    """Delete the retry copy and forget it. Done once a file no longer needs a
    retry — i.e. it succeeded, was resolved, or its tracker row is deleted — so
    only failed / needs-review files keep an original around."""
    raw_store.delete_raw(t.raw_path)
    t.raw_path = None


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


def _union_buckets(entries: list) -> tuple[dict, list[str]]:
    """Union each bucket across all contributing files; flag dates that two
    DIFFERENT files both claim (overlapping weekly sheets), except public
    holidays which legitimately repeat on every sheet."""
    merged: dict[str, list[str]] = {b: [] for b in BUCKET_FIELDS}
    overlap_flags: list[str] = []
    for b in BUCKET_FIELDS:
        seen: dict[str, str] = {}  # date -> first filename that claimed it
        for e in entries:
            fname = e.get("filename") or "file"
            for d in (e.get("buckets", {}).get(b) or []):
                if d in seen:
                    if b != "public_holiday" and seen[d] != fname:
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
        # id alone is NOT enough (shared across teams) — require the name too.
        if employee_id and r.employee_id == employee_id:
            if name_norm and (r.employee_name or "").strip().lower() == name_norm:
                return r
            if not name_norm:
                return r
        elif name_norm and (r.employee_name or "").strip().lower() == name_norm:
            return r
    return None


# ---------------------------------------------------------------------------
# The core unit
# ---------------------------------------------------------------------------
async def ingest_manual_entry(
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
        for (fn, _ct, dat) in attachments:
            # Store exactly what was handed in — the .eml (or the uploaded
            # sheet) as one file. Nested attachments/inline images inside an
            # .eml are NOT filed separately: they already live inside the
            # .eml itself, and filing them again duplicated real sheets and
            # littered the vault with signature logos/banners.
            sp.save_file(account_manager, employee_name, month, year, fn, dat)
        folder_rel = sp.folder_rel(account_manager, employee_name, month, year)
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


async def _tracker_bytes(tracker: PipelineFile) -> tuple[bytes, str]:
    """The raw bytes for a tracker: the stored retry copy, else refetched from
    the email provider when the item is a single provider attachment."""
    data = read_raw_copy(tracker)
    filename = tracker.filename or "file"
    if (data is None and tracker.source_kind == "email" and tracker.source_id
            and tracker.attachment_id and not str(tracker.attachment_id).startswith("__")):
        provider = get_email_provider()
        data, filename, _ct = await provider.get_attachment_bytes(
            tracker.source_id, tracker.attachment_id)
    if data is None:
        raise FileNotFoundError("The original file is no longer available.")
    return data, filename


async def retry_pipeline_file(db: AsyncSession, tracker: PipelineFile) -> tuple[TimesheetRecord | None, PipelineFile]:
    """Re-run the ANALYSIS for a tracked file and re-stage it for review —
    retry never files a record directly (everything goes through Compare & Fix)."""
    from app.services.agents import full_email_extract as fx
    from app.services.extract_email import auto_accept
    from app.services.extract_email.staging import sheet_summaries
    from app.services.extraction.validation import summarize as summarize_record
    from app.services.extraction.validation import validate

    data, filename = await _tracker_bytes(tracker)
    analysis = await fx.analyse_upload(db, filename=filename, data=data)
    groups = analysis["groups"]
    tracker.events = (tracker.events or []) + [{
        "stage": PipelineStage.EXTRACTION,
        "status": "ok" if groups else "fail",
        "detail": (f"Retry re-analysed the file: {len(groups)} group(s) found."
                   if groups else "Retry re-analysed the file: no timesheet or certificate found."),
        "at": _now_iso(),
    }]
    if not groups:
        tracker.status = PipelineStatus.FAILED
        tracker.failure_code = FailureCode.EXTRACTION_UNREADABLE
        tracker.failure_detail = "No timesheet or certificate could be read from this file."
        await db.commit()
        await db.refresh(tracker)
        return None, tracker

    primary = max(groups, key=lambda g: sum(len(v) for v in g["buckets"].values()))
    month, year = primary["month"], primary["year"]
    if month and year:
        cleaned, val_flags = validate(primary["buckets"], month, year)
        summary = summarize_record(cleaned, val_flags, month, year, len(primary["sheets"]))
    else:
        cleaned, val_flags = primary["buckets"], ["No usable month/year — pick the period."]
        summary = "Could not read a month/year — pick the period in Compare & Fix."
    flags = list(dict.fromkeys(primary["overlap_flags"] + primary["fold_notes"] + val_flags))

    # The accept/hold verdict must be recomputed here too — the buckets and
    # employee match above are FRESH from this retry, and leaving the old
    # decision in extraction_meta would show a reviewer a "why held" reason
    # (or an "AI recommends") left over from the run before the retry.
    g_for_eval = {**primary, "buckets": cleaned}
    decision = auto_accept.evaluate(g_for_eval, extra_flags=val_flags)

    tracker.employee_id = primary["employee_id"]
    tracker.employee_name = primary["name"]
    tracker.month, tracker.year = month, year
    tracker.status = PipelineStatus.NEEDS_REVIEW
    tracker.failure_code = FailureCode.PENDING_REVIEW
    if decision.accepted:
        tracker.failure_detail = ("Re-extracted. AI recommends accepting — "
                                   "review the leaves and press Accept to file.")
    else:
        hold = ("; ".join(decision.blockers[:3]) if decision.blockers
                 else "needs a human check")
        tracker.failure_detail = f"Re-extracted. Held for review: {hold}"
    tracker.extraction_method = analysis["run_meta"].get("method")
    tracker.extraction_model = analysis["run_meta"].get("model")
    prior_fee = (tracker.extraction_meta or {}).get("full_email_extract") or {}
    tracker.extraction_meta = {
        **(tracker.extraction_meta or {}),
        "staged": {
            "employee_pk": primary["employee_pk"],
            "matched_name": primary["name"],
            "matched_employee_id": primary["employee_id"],
            "month": month, "year": year,
            "buckets": cleaned,
            "validation_status": "manual_review" if flags else "verified",
            "flags": flags,
            "summary": f"{summary} {analysis['approval']['detail']}",
            "extraction_status": "ok",
        },
        "auto_accept": decision.as_meta(),
        "full_email_extract": {
            **prior_fee,
            "method": analysis["run_meta"].get("method"),
            "model": analysis["run_meta"].get("model"),
            "match_note": primary.get("note"),
            "approval": analysis["approval"],
            "sheets": sheet_summaries(primary["sheets"]),
        },
    }
    await db.commit()
    await db.refresh(tracker)
    return None, tracker
