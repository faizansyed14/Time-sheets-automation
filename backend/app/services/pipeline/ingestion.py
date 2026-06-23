"""
Ingestion pipeline — shared by BOTH the email "Accept" action and the Upload page.

Core unit: ingest_timesheet_bytes(...) takes one timesheet's bytes and walks it
through tracked stages (every file gets a PipelineFile audit row):

  received -> protection_check -> extraction (LLM) -> identification
  -> matching -> validation -> filing -> recorded

Outcomes:
  - success       record created (or merged into the month's record)
  - needs_review  record created but flagged (validation mismatch, ID/name
                  disagreement, storage problem)
  - failed        nothing recorded; failure_code says exactly where it died
                  (protected_pdf, llm_failed, name_not_found, month_not_found,
                   employee_not_matched, ambiguous_id, unsupported_type, ...)

MULTI-FILE MONTHS: clients sending weekly / 15-day sheets produce several files
for the same employee + month. Each file's extracted buckets are stored as an
entry in TimesheetRecord.source_files and the record's buckets are the UNION of
all entries — so a second file MERGES into the month instead of being treated
as a duplicate. Re-uploading the same file replaces its own entry (idempotent).

DUPLICATE IDs (AUH vs DXB): matching uses employee_id AND name (see
services/matching.py); an ID shared across teams is resolved by the name, and
an unresolvable one fails as `ambiguous_id` instead of filing under the wrong
person.
"""
from __future__ import annotations

import calendar
import json as _json
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.email_message import EmailMessage, EmailStatus
from app.models.employee import Employee
from app.models.pipeline_file import FailureCode, PipelineFile, PipelineStage, PipelineStatus
from app.models.timesheet_record import ApprovalStatus, TimesheetRecord, ValidationStatus
from app.services.pipeline import matching
from app.services.pipeline import raw_store
from app.services import storage_provider as sp
from app.services.email_provider import get_email_provider
from app.services.extraction import get_extraction_engine
from app.services.extraction.base import ApprovalExtraction
from app.services.extraction.file_processor import (
    detect_file_type,
    eml_attachment_save_name,
    eml_best_attachment,
    eml_body_to_images,
)
from app.services.extraction.validation import summarize as summarize_record
from app.services.extraction.validation import validate
from app.services.pipeline.matching import MatchCode

BUCKET_FIELDS = {
    "annual": "annual_leave_dates",
    "remote": "remote_work_dates",
    "sick": "sick_leave_dates",
    "unpaid": "unpaid_leave_dates",
    "absent": "absent_dates",
    "public_holiday": "public_holiday_dates",
}

# Failed / flagged files a reviewer can complete by picking the right employee.
RESOLVABLE_MATCH_CODES = frozenset({
    FailureCode.AMBIGUOUS_ID,
    FailureCode.EMPLOYEE_NOT_MATCHED,
    FailureCode.ID_NAME_MISMATCH,
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


def _pdf_is_protected(data: bytes) -> bool:
    """True if the PDF needs a password to open."""
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(stream=data, filetype="pdf")
        try:
            return bool(doc.needs_pass)
        finally:
            doc.close()
    except Exception:
        # PyMuPDF unavailable/failed — fall back to a byte scan.
        return b"/Encrypt" in data


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
async def ingest_timesheet_bytes(
    db: AsyncSession, *, data: bytes, filename: str, content_type: str,
    approval_detected: bool, approval_detail: str, approval_bytes: bytes | None,
    approval_name: str, source_id: str | None, attachment_id: str | None = None,
    source_kind: str = "upload", tracker: PipelineFile | None = None,
    manual_employee_pk: str | None = None,
    manual_month: int | None = None,
    manual_year: int | None = None,
    resolution_note: str | None = None,
) -> tuple[TimesheetRecord | None, PipelineFile]:
    """Process one timesheet file. Always returns the PipelineFile audit row;
    the TimesheetRecord is None when the file failed before being recorded."""
    # ---- stage: received ----
    if tracker is None:
        tracker = PipelineFile(
            filename=filename, content_type=content_type, size_bytes=len(data or b""),
            source_kind=source_kind, source_id=source_id, attachment_id=attachment_id,
        )
        db.add(tracker)
        # Populate the primary key now so the raw-copy folder (data/pipeline_raw/<id>/)
        # has a real id — otherwise uploads could never be retried.
        await db.flush()
    else:  # retry: reset the previous outcome
        tracker.status = PipelineStatus.PROCESSING
        tracker.failure_code = None
        tracker.failure_detail = None
        tracker.resolved_at = None
        tracker.resolution_note = None
    _event(tracker, PipelineStage.RECEIVED, "ok",
           f"Received {filename} ({len(data or b'')} bytes) from {source_kind}.")
    if not tracker.raw_path:
        _save_raw_copy(tracker, filename, data or b"")

    # ---- stage: protection / type check ----
    if not data:
        _fail(tracker, PipelineStage.PROTECTION_CHECK, FailureCode.EMPTY_FILE,
              "The file is empty (0 bytes).")
        return None, tracker
    ftype = detect_file_type(filename, data)
    if ftype == "unknown":
        _fail(tracker, PipelineStage.PROTECTION_CHECK, FailureCode.UNSUPPORTED_TYPE,
              f"Unsupported file type for '{filename}'. Accepted: PDF, DOCX, XLSX, image, EML.")
        return None, tracker
    if ftype == "pdf" and _pdf_is_protected(data):
        _fail(tracker, PipelineStage.PROTECTION_CHECK, FailureCode.PROTECTED_PDF,
              "This PDF is password-protected and cannot be read. "
              "Ask the sender for an unprotected copy (or the password) and retry.")
        return None, tracker
    _event(tracker, PipelineStage.PROTECTION_CHECK, "ok",
           f"File type '{ftype}' accepted; not password-protected.")

    # ---- stage: extraction (LLM) ----
    engine = get_extraction_engine()
    try:
        ext = await engine.extract_timesheet(
            data, filename, content_type, source_id or "", attachment_id or filename)
    except Exception as e:  # missing key, API/network error, bad file ...
        _fail(tracker, PipelineStage.EXTRACTION, FailureCode.LLM_FAILED,
              f"The extraction model failed on this file: {str(e)[:300]}")
        return None, tracker
    _event(tracker, PipelineStage.EXTRACTION, "ok",
           f"LLM extraction returned (name='{ext.employee_name or '—'}', "
           f"id='{ext.employee_id or '—'}', period={ext.month}/{ext.year}).")

    # ---- stage: identification (did the sheet contain usable identity/period?) ----
    has_identity = bool((ext.employee_name or "").strip() or (ext.employee_id or "").strip())
    has_month = bool(1 <= (ext.month or 0) <= 12 and (ext.year or 0) >= 2000)
    has_manual_period = (
        manual_month is not None and manual_year is not None
        and 1 <= manual_month <= 12 and manual_year >= 2000
    )
    if not has_identity and not has_month and not has_manual_period and not manual_employee_pk:
        _fail(tracker, PipelineStage.IDENTIFICATION, FailureCode.EXTRACTION_UNREADABLE,
              f"Nothing usable could be read from the sheet (no name, no ID, no month). "
              f"Engine said: {ext.summary or 'no detail'}")
        return None, tracker
    if not has_identity and not manual_employee_pk:
        _fail(tracker, PipelineStage.IDENTIFICATION, FailureCode.NAME_NOT_FOUND,
              "No employee name or employee ID could be found on the sheet.")
        return None, tracker
    period_month = manual_month if manual_month is not None else ext.month
    period_year = manual_year if manual_year is not None else ext.year
    if not (1 <= (period_month or 0) <= 12 and (period_year or 0) >= 2000):
        if manual_month is not None or manual_year is not None:
            _fail(tracker, PipelineStage.IDENTIFICATION, FailureCode.MONTH_NOT_FOUND,
                  f"Invalid period selected (month={period_month}, year={period_year}).")
        else:
            _fail(tracker, PipelineStage.IDENTIFICATION, FailureCode.MONTH_NOT_FOUND,
                  f"No usable month/year on the sheet (got month={ext.month}, year={ext.year}).")
        return None, tracker
    ext.month, ext.year = period_month, period_year
    tracker.month, tracker.year = period_month, period_year
    tracker.employee_name = ext.employee_name
    tracker.employee_id = ext.employee_id
    _event(tracker, PipelineStage.IDENTIFICATION, "ok",
           f"Sheet identifies '{ext.employee_name or ext.employee_id}' for "
           f"{calendar.month_name[period_month]} {period_year}.")

    # ---- stage: matching (employee_id AND name must agree — AUH/DXB share IDs) ----
    if manual_employee_pk:
        matched = (
            await db.execute(select(Employee).where(Employee.id == manual_employee_pk))
        ).scalar_one_or_none()
        if not matched:
            _fail(tracker, PipelineStage.MATCHING, FailureCode.EMPLOYEE_NOT_MATCHED,
                  "The selected employee was not found in the matcher list.")
            return None, tracker
        loc = f" [{matched.location}]" if matched.location else ""
        m_note = (
            (resolution_note or "").strip()
            or f"Manually assigned to {matched.name} ({matched.employee_id}{loc})."
        )
        _event(tracker, PipelineStage.MATCHING, "ok", m_note)
        m = matching.MatchResult(matched, m_note, MatchCode.ID_AND_NAME)
    else:
        m = await matching.match_employee(db, ext.employee_id, ext.employee_name)
        if m.employee is None:
            code = FailureCode.AMBIGUOUS_ID if m.code == MatchCode.AMBIGUOUS_ID \
                else FailureCode.EMPLOYEE_NOT_MATCHED
            _fail(tracker, PipelineStage.MATCHING, code, m.note)
            return None, tracker
        matched = m.employee
        _event(tracker, PipelineStage.MATCHING, "ok", m.note)
    employee_name = matched.name
    account_manager = matched.account_manager
    tracker.employee_name = employee_name
    tracker.employee_id = matched.employee_id

    cal_days = calendar.monthrange(period_year, period_month)[1]

    # ---- stage: validation + multi-file merge ----
    existing = await _find_existing(db, matched.id, matched.employee_id,
                                    employee_name, period_month, period_year)
    key = _file_key(source_id, attachment_id, filename)
    new_entry = {
        "key": key, "filename": filename, "source_id": source_id,
        "attachment_id": attachment_id, "ingested_at": _now_iso(),
        "buckets": {
            "annual": ext.annual_leave_dates or [], "remote": ext.remote_work_dates or [],
            "sick": ext.sick_leave_dates or [], "unpaid": ext.unpaid_leave_dates or [],
            "absent": ext.absent_dates or [], "public_holiday": ext.public_holiday_dates or [],
        },
    }
    prior_entries = (existing.source_files if existing else []) or []
    same_key_prior = next((e for e in prior_entries if e.get("key") == key), None)
    if same_key_prior and same_key_prior.get("buckets") == new_entry["buckets"]:
        _event(tracker, PipelineStage.VALIDATION, "warn",
               "Identical file already processed for this month — no changes made.")
    entries = _merge_source_files(prior_entries, new_entry)
    merged, overlap_flags = _union_buckets(entries)
    cleaned, flags = validate(merged, period_month, period_year)
    flags = list(dict.fromkeys((ext.hr_flags or []) + overlap_flags + flags))
    validation_status = ValidationStatus.MANUAL_REVIEW if flags else ValidationStatus.VERIFIED
    if len(entries) > 1:
        _event(tracker, PipelineStage.VALIDATION,
               "warn" if flags else "ok",
               f"Merged into existing {calendar.month_name[period_month]} {period_year} record "
               f"({len(entries)} files now cover this month)."
               + (f" {len(flags)} flag(s) raised." if flags else ""))
    else:
        _event(tracker, PipelineStage.VALIDATION, "warn" if flags else "ok",
               ("Validation raised: " + " ".join(flags[:4])) if flags else "Validation clean.")

    mname = calendar.month_name[period_month]
    # Clean, human-readable summary. Prefer the engine's polished LLM note
    # (vision mode); always fall back to the deterministic summarizer so the
    # record never shows a raw dump of dates.
    summary_ctx = {
        "employee": employee_name, "month": period_month, "year": period_year,
        "leaves": cleaned, "issues": flags, "n_files": len(entries),
    }
    summary: str | None = None
    try:
        summary = await engine.summarize(summary_ctx)
    except Exception:
        summary = None
    if not summary:
        summary = summarize_record(cleaned, flags, period_month, period_year, len(entries))

    # ---- stage: filing on disk (best-effort; never blocks record creation) ----
    folder_rel = None
    storage_warn = None
    eml_extracted_name: str | None = None
    try:
        sp.save_file(account_manager, employee_name, period_month, period_year, filename, data)
        if ftype == "eml":
            best = eml_best_attachment(data)
            if best:
                raw_name, att_payload, att_ftype = best
                eml_extracted_name = eml_attachment_save_name(raw_name, att_ftype)
                sp.save_file(
                    account_manager, employee_name, period_month, period_year,
                    eml_extracted_name, att_payload,
                )
            else:
                # Inline timesheet (no attachment): save the rendered image of
                # the email (Subject + body) — the same image sent to the model.
                try:
                    imgs = eml_body_to_images(data)
                    for idx, img in enumerate(imgs[:8], 1):
                        nm = "email_view.jpg" if len(imgs) == 1 else f"email_view_p{idx}.jpg"
                        sp.save_file(account_manager, employee_name, period_month, period_year, nm, img)
                    if imgs:
                        eml_extracted_name = "email_view.jpg"
                except Exception:
                    pass
        if approval_bytes is not None:
            sp.save_file(account_manager, employee_name, period_month, period_year, approval_name, approval_bytes)
        sp.save_text(account_manager, employee_name, period_month, period_year, "extraction_result.json", _json.dumps({
            "employee": {"extracted_id": ext.employee_id, "extracted_name": ext.employee_name,
                         "matched_id": matched.employee_id, "matched_name": matched.name,
                         "location": matched.location, "dco_number": matched.dco_number,
                         "account_manager": account_manager, "match_note": m.note},
            "period": {"month": period_month, "year": period_year},
            "source_files": [{"filename": e["filename"], "ingested_at": e["ingested_at"]} for e in entries],
            "eml": {"original": filename, "extracted_attachment": eml_extracted_name} if ftype == "eml" else None,
            "leaves": {k: cleaned[k] for k in BUCKET_FIELDS},
            "validation": {"status": validation_status, "summary": summary, "flags": flags},
            "approval": {"detected": approval_detected, "detail": approval_detail},
            "source": source_id, "ingested_at": _now_iso(),
        }, indent=2, default=str))
        folder_rel = sp.folder_rel(account_manager, employee_name, period_month, period_year)
        filed = f"Filed under {folder_rel}."
        if eml_extracted_name:
            filed += f" Extracted attachment saved as {eml_extracted_name}."
        _event(tracker, PipelineStage.FILING, "ok", filed)
    except Exception as e:
        storage_warn = f"Could not file on disk: {str(e)[:200]}"
        _event(tracker, PipelineStage.FILING, "warn", storage_warn)

    # ---- stage: record upsert ----
    rec = existing or TimesheetRecord()
    rec.extracted_employee_id = ext.employee_id
    rec.extracted_employee_name = ext.employee_name
    rec.matched_employee_pk = matched.id
    rec.employee_id = matched.employee_id
    rec.employee_name = employee_name
    rec.account_manager = account_manager
    rec.dco_number = matched.dco_number
    rec.match_note = m.note
    rec.month = period_month
    rec.year = period_year
    rec.calendar_days = cal_days
    for bucket, field in BUCKET_FIELDS.items():
        setattr(rec, field, cleaned[bucket])
    rec.source_files = entries
    rec.validation_status = validation_status
    rec.llm_summary = summary
    rec.hr_flags = flags
    rec.approval_detected = bool(approval_detected or (existing and existing.approval_detected))
    if approval_detected or not existing:
        rec.approval_detail = approval_detail
    if not existing:
        # Manual uploads start pending; email flow auto-approves (per requirement).
        is_upload = bool(source_id and source_id.startswith("upload:"))
        rec.approval_status = ApprovalStatus.PENDING if is_upload else ApprovalStatus.APPROVED
    rec.source_email_id = source_id
    if folder_rel:
        rec.storage_folder = folder_rel
    if not existing:
        db.add(rec)
    await db.flush()

    # ---- stage: recorded ----
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
        tracker.failure_code = None
        tracker.failure_detail = None
    _event(tracker, PipelineStage.RECORDED,
           "ok" if tracker.status == PipelineStatus.SUCCESS else "warn",
           f"Record {'updated' if existing else 'created'} for {employee_name} — "
           f"{mname} {period_year} ({len(entries)} file(s) on this month).")
    if manual_employee_pk and resolution_note:
        tracker.resolution_note = resolution_note.strip()
    # Success => the retry copy is no longer needed (re-upload if you ever must
    # re-run a clean file). Only failed / needs-review files keep their original.
    if tracker.status == PipelineStatus.SUCCESS:
        purge_raw_copy(tracker)
    return rec, tracker


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------
async def _safe_approval(engine, data, source_id, attachment_id) -> ApprovalExtraction:
    try:
        return await engine.extract_approval(data, source_id or "", attachment_id or "")
    except Exception as e:
        return ApprovalExtraction(detected=False, detail=f"Could not read approval ({str(e)[:120]}).")


async def ingest_email(db: AsyncSession, email: EmailMessage) -> list[TimesheetRecord]:
    provider = get_email_provider()
    engine = get_extraction_engine()
    attachments = email.attachments or []
    approval_atts = [a for a in attachments if a.get("kind") == "approval_screenshot"]
    timesheet_atts = [a for a in attachments if a.get("kind") == "timesheet"]

    approval_detected, approval_detail = False, "No approval screenshot provided."
    approval_bytes, approval_name = None, "manager_approval.png"
    if approval_atts:
        a = approval_atts[0]
        try:
            approval_bytes, approval_name, _ = await provider.get_attachment_bytes(
                email.provider_message_id, a["attachment_id"])
            ap = await _safe_approval(engine, approval_bytes, email.provider_message_id, a["attachment_id"])
            approval_detected, approval_detail = ap.detected, ap.detail
        except Exception:
            approval_bytes = None

    created: list[TimesheetRecord] = []
    for a in timesheet_atts:
        try:
            data, filename, content_type = await provider.get_attachment_bytes(
                email.provider_message_id, a["attachment_id"])
        except Exception as e:
            t = PipelineFile(
                filename=a.get("filename") or a["attachment_id"], content_type=a.get("content_type"),
                size_bytes=a.get("size"), source_kind="email",
                source_id=email.provider_message_id, attachment_id=a["attachment_id"])
            db.add(t)
            _fail(t, PipelineStage.RECEIVED, FailureCode.UNKNOWN,
                  f"Could not fetch the attachment from the mailbox: {str(e)[:200]}")
            continue
        rec, _t = await ingest_timesheet_bytes(
            db, data=data, filename=filename, content_type=content_type,
            approval_detected=approval_detected, approval_detail=approval_detail,
            approval_bytes=approval_bytes, approval_name=approval_name,
            source_id=email.provider_message_id, attachment_id=a["attachment_id"],
            source_kind="email")
        if rec is not None:
            created.append(rec)

    email.status = EmailStatus.INGESTED
    email.decided_at = datetime.now(timezone.utc)
    await db.commit()
    for r in created:
        await db.refresh(r)
    return created


async def ingest_upload(
    db: AsyncSession, *, filename: str, content_type: str, data: bytes,
) -> tuple[TimesheetRecord | None, PipelineFile]:
    rec, tracker = await ingest_timesheet_bytes(
        db, data=data, filename=filename, content_type=content_type,
        approval_detected=False, approval_detail="Uploaded manually (no email approval screenshot).",
        approval_bytes=None, approval_name="manager_approval.png",
        source_id=f"upload:{filename}", source_kind="upload")
    await db.commit()
    if rec is not None:
        await db.refresh(rec)
    await db.refresh(tracker)
    return rec, tracker


def can_resolve_assign(tracker: PipelineFile) -> bool:
    if tracker.status not in (PipelineStatus.FAILED, PipelineStatus.NEEDS_REVIEW):
        return False
    if tracker.failure_code not in RESOLVABLE_MATCH_CODES:
        return False
    if tracker.raw_path:
        return True
    return bool(tracker.source_kind == "email" and tracker.source_id and tracker.attachment_id)


async def resolve_pipeline_with_employee(
    db: AsyncSession,
    tracker: PipelineFile,
    *,
    employee_pk: str,
    month: int,
    year: int,
    note: str | None = None,
) -> tuple[TimesheetRecord | None, PipelineFile]:
    """Re-run extraction and file the timesheet under a manually chosen employee."""
    if not can_resolve_assign(tracker):
        raise ValueError("This pipeline file cannot be completed with a manual employee assignment.")
    data = read_raw_copy(tracker)
    filename, content_type = tracker.filename, tracker.content_type or "application/octet-stream"
    if data is None and tracker.source_kind == "email" and tracker.source_id and tracker.attachment_id:
        provider = get_email_provider()
        data, filename, content_type = await provider.get_attachment_bytes(
            tracker.source_id, tracker.attachment_id)
    if data is None:
        raise FileNotFoundError("The original file is no longer available.")
    tracker.events = []
    rec, tracker = await ingest_timesheet_bytes(
        db, data=data, filename=filename, content_type=content_type,
        approval_detected=False,
        approval_detail="Manually assigned by reviewer from the pipeline tracker.",
        approval_bytes=None, approval_name="manager_approval.png",
        source_id=tracker.source_id, attachment_id=tracker.attachment_id,
        source_kind=tracker.source_kind, tracker=tracker,
        manual_employee_pk=employee_pk, manual_month=month, manual_year=year,
        resolution_note=note,
    )
    await db.commit()
    if rec is not None:
        await db.refresh(rec)
    await db.refresh(tracker)
    return rec, tracker


async def retry_pipeline_file(db: AsyncSession, tracker: PipelineFile) -> tuple[TimesheetRecord | None, PipelineFile]:
    """Re-run the pipeline for a tracked file (after the cause was fixed)."""
    data = read_raw_copy(tracker)
    filename, content_type = tracker.filename, tracker.content_type or "application/octet-stream"
    if data is None and tracker.source_kind == "email" and tracker.source_id and tracker.attachment_id:
        provider = get_email_provider()
        data, filename, content_type = await provider.get_attachment_bytes(
            tracker.source_id, tracker.attachment_id)
    if data is None:
        raise FileNotFoundError("The original file is no longer available for retry.")
    tracker.events = []
    rec, tracker = await ingest_timesheet_bytes(
        db, data=data, filename=filename, content_type=content_type,
        approval_detected=False, approval_detail="Re-processed from the pipeline tracker.",
        approval_bytes=None, approval_name="manager_approval.png",
        source_id=tracker.source_id, attachment_id=tracker.attachment_id,
        source_kind=tracker.source_kind, tracker=tracker)
    await db.commit()
    if rec is not None:
        await db.refresh(rec)
    await db.refresh(tracker)
    return rec, tracker
