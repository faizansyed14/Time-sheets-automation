"""
Ingestion pipeline — shared by email Accept, Upload (stage → review), and
Agentic Chat store (direct file).

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
    email_body_to_images,
    eml_all_attachments,
    eml_body_to_images,
)
from app.services.extraction.validation import summarize as summarize_record
from app.services.extraction.validation import validate
from app.services.pipeline.matching import MatchCode

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
    FailureCode.PENDING_REVIEW,    # AI-extracted, staged for accept via Compare & Fix
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
    email_employee_pk: str | None = None,
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
    # Record how this file was read so the tracker can show cost/provenance
    # (which GPT model, deterministic-no-LLM, or local OCR) per file.
    tracker.extraction_model = getattr(ext, "extraction_model", None)
    tracker.extraction_method = getattr(ext, "extraction_method", None)
    tracker.used_ocr = bool(getattr(ext, "used_ocr", False))
    tracker.extraction_meta = {
        **(getattr(ext, "extraction_meta", None) or {}),
        "source_kind": source_kind,
        "content_type": content_type,
        "size_bytes": len(data or b""),
        "model": tracker.extraction_model,
        "method": tracker.extraction_method,
        "used_ocr": tracker.used_ocr,
    }
    _method_label = {
        "vision-llm": f"{tracker.extraction_model or 'vision model'}",
        "deterministic-text": "deterministic text parser (no LLM)",
        "mock": "mock engine (no LLM)",
        "unsupported": "unsupported file",
    }.get(tracker.extraction_method or "", tracker.extraction_method or "engine")
    _ocr_note = " · OCR text layer used" if tracker.used_ocr else ""
    _event(tracker, PipelineStage.EXTRACTION, "ok",
           f"Read with {_method_label}{_ocr_note} "
           f"(name='{ext.employee_name or '—'}', "
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
        email_hint = None
        if email_employee_pk:
            email_hint = (
                await db.execute(select(Employee).where(Employee.id == email_employee_pk))
            ).scalar_one_or_none()
        m = await matching.match_employee(
            db, ext.employee_id, ext.employee_name, email_hint=email_hint)
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
            "sick": ext.sick_leave_dates or [], "maternity": ext.maternity_leave_dates or [],
            "unpaid": ext.unpaid_leave_dates or [],
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
    eml_extracted_names: list[str] = []
    try:
        # Always keep the ORIGINAL incoming file in the vault (the .eml itself,
        # the PDF, the image, …) so the source of every record is preserved.
        sp.save_file(account_manager, employee_name, period_month, period_year, filename, data)
        if ftype == "eml":
            # Store EACH attached sheet separately next to the original .eml
            # (e.g. Sri_Timesheet_May2026.pdf), so the vault holds the mail AND
            # every attachment AND the .json result — not just the mail.
            attachments = eml_all_attachments(data)
            if attachments:
                for save_name, att_payload, _att_ftype in attachments:
                    sp.save_file(
                        account_manager, employee_name, period_month, period_year,
                        save_name, att_payload,
                    )
                    eml_extracted_names.append(save_name)
                eml_extracted_name = eml_extracted_names[0]
            else:
                # Inline timesheet (no attachment): save the rendered image of
                # the email (Subject + body) — the same image sent to the model.
                try:
                    imgs = eml_body_to_images(data)
                    for idx, img in enumerate(imgs[:8], 1):
                        nm = "email_view.jpg" if len(imgs) == 1 else f"email_view_p{idx}.jpg"
                        sp.save_file(account_manager, employee_name, period_month, period_year, nm, img)
                        eml_extracted_names.append(nm)
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
            "eml": {"original": filename, "extracted_attachment": eml_extracted_name,
                    "extracted_attachments": eml_extracted_names} if ftype == "eml" else None,
            "extraction": tracker.extraction_meta or {
                "model": tracker.extraction_model, "method": tracker.extraction_method,
                "used_ocr": bool(tracker.used_ocr)},
            "leaves": {k: cleaned[k] for k in BUCKET_FIELDS},
            "validation": {"status": validation_status, "summary": summary, "flags": flags},
            "approval": {"detected": approval_detected, "detail": approval_detail},
            "source": source_id, "ingested_at": _now_iso(),
        }, indent=2, default=str))
        folder_rel = sp.folder_rel(account_manager, employee_name, period_month, period_year)
        filed = f"Filed under {folder_rel}."
        if eml_extracted_names:
            if len(eml_extracted_names) == 1:
                filed += f" Original email kept; attached sheet saved as {eml_extracted_names[0]}."
            else:
                filed += (f" Original email kept; {len(eml_extracted_names)} attached file(s) saved "
                          f"({', '.join(eml_extracted_names)}).")
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


class IngestSelectionError(ValueError):
    """Invalid attachment selection for email ingestion."""


def resolve_ingest_attachments(
    attachments: list[dict],
    *,
    attachment_ids: list[str] | None,
    approval_attachment_id: str | None,
    extract_body: bool = False,
) -> tuple[list[dict], dict | None]:
    """Pick timesheet + optional approval attachments for extraction."""
    by_id = {a["attachment_id"]: a for a in attachments if a.get("attachment_id")}

    if attachment_ids is None:
        timesheet_atts = [a for a in attachments if a.get("kind") == "timesheet"]
        if approval_attachment_id:
            approval_att = by_id.get(approval_attachment_id)
            if not approval_att:
                raise IngestSelectionError("Unknown approval attachment.")
            if approval_att.get("kind") != "approval_screenshot":
                raise IngestSelectionError("Approval selection must be a screenshot attachment.")
        else:
            approval_att = next((a for a in attachments if a.get("kind") == "approval_screenshot"), None)
        return timesheet_atts, approval_att

    if not attachment_ids:
        if extract_body:
            approval_att = None
            if approval_attachment_id:
                approval_att = by_id.get(approval_attachment_id)
                if not approval_att:
                    raise IngestSelectionError("Unknown approval attachment.")
            return [], approval_att
        raise IngestSelectionError("Select at least one timesheet attachment to extract.")

    timesheet_atts: list[dict] = []
    for aid in attachment_ids:
        att = by_id.get(aid)
        if not att:
            raise IngestSelectionError(f"Unknown attachment id: {aid}")
        if aid == approval_attachment_id:
            raise IngestSelectionError("Same attachment cannot be timesheet and approval.")
        timesheet_atts.append(att)

    approval_att = None
    if approval_attachment_id:
        approval_att = by_id.get(approval_attachment_id)
        if not approval_att:
            raise IngestSelectionError("Unknown approval attachment.")
        if approval_attachment_id in attachment_ids:
            raise IngestSelectionError("Same attachment cannot be timesheet and approval.")

    return timesheet_atts, approval_att


async def ingest_email(
    db: AsyncSession,
    email: EmailMessage,
    *,
    attachment_ids: list[str] | None = None,
    approval_attachment_id: str | None = None,
    extract_body: bool = False,
) -> list[TimesheetRecord]:
    provider = get_email_provider()
    engine = get_extraction_engine()
    attachments = email.attachments or []
    timesheet_atts, approval_att = resolve_ingest_attachments(
        attachments,
        attachment_ids=attachment_ids,
        approval_attachment_id=approval_attachment_id,
        extract_body=extract_body,
    )

    if not extract_body and not timesheet_atts:
        raise IngestSelectionError("Select at least one timesheet attachment to extract.")

    approval_detected, approval_detail = False, "No approval screenshot provided."
    approval_bytes, approval_name = None, "manager_approval.png"
    if approval_att:
        try:
            approval_bytes, approval_name, _ = await provider.get_attachment_bytes(
                email.provider_message_id, approval_att["attachment_id"])
            ap = await _safe_approval(
                engine, approval_bytes, email.provider_message_id, approval_att["attachment_id"])
            approval_detected, approval_detail = ap.detected, ap.detail
        except Exception:
            approval_bytes = None

    from app.services.inbox.employee_match import match_sender

    sm = await match_sender(db, sender_email=email.sender_email, body_text=email.body_text)
    inbox_employee_pk: str | None = sm["employee_pk"] if sm else None

    created: list[TimesheetRecord] = []

    if extract_body:
        try:
            imgs = email_body_to_images(email.subject, email.body_text)
        except Exception as e:
            raise IngestSelectionError(f"Could not render email body as image: {str(e)[:120]}") from e
        if not imgs:
            raise IngestSelectionError("Could not render email body as image.")
        rec, _t = await ingest_timesheet_bytes(
            db, data=imgs[0], filename="email_body.jpg", content_type="image/jpeg",
            approval_detected=approval_detected, approval_detail=approval_detail,
            approval_bytes=approval_bytes, approval_name=approval_name,
            source_id=email.provider_message_id, attachment_id="__body__",
            source_kind="email", email_employee_pk=inbox_employee_pk)
        if rec is not None:
            created.append(rec)

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
            source_kind="email", email_employee_pk=inbox_employee_pk)
        if rec is not None:
            created.append(rec)

    email.status = EmailStatus.INGESTED
    email.decided_at = datetime.now(timezone.utc)
    await db.commit()
    for r in created:
        await db.refresh(r)
    return created


async def _stage_bytes_for_review(
    db: AsyncSession,
    *,
    filename: str,
    content_type: str,
    data: bytes,
    source_kind: str,
    source_id: str,
    attachment_id: str | None = None,
) -> PipelineFile:
    """Shared Run Extraction path — extract one file, stage for Compare & Fix."""
    from app.services.agents.extract_service import extract_from_upload

    tracker: PipelineFile | None = None
    if attachment_id is not None:
        tracker = (await db.execute(select(PipelineFile).where(
            PipelineFile.source_kind == source_kind,
            PipelineFile.source_id == source_id,
            PipelineFile.attachment_id == attachment_id,
            PipelineFile.failure_code == FailureCode.PENDING_REVIEW,
        ))).scalars().first()
    if tracker is None:
        tracker = PipelineFile(
            filename=filename, content_type=content_type, size_bytes=len(data or b""),
            source_kind=source_kind, source_id=source_id, attachment_id=attachment_id)
        db.add(tracker)
        await db.flush()
    if not tracker.raw_path:
        _save_raw_copy(tracker, filename, data or b"")

    result = await extract_from_upload(
        db, filename=filename, content_type=content_type, data=data)
    matched = result.get("matched_employee") or {}
    tracker.employee_id = matched.get("employee_id") or result.get("extracted_employee_id")
    tracker.employee_name = matched.get("name") or result.get("extracted_employee_name")
    tracker.month = result.get("month")
    tracker.year = result.get("year")
    tracker.status = PipelineStatus.NEEDS_REVIEW
    tracker.failure_code = FailureCode.PENDING_REVIEW
    tracker.failure_detail = "AI-extracted — review the leaves and accept to file the record."
    tracker.extraction_meta = {
        "staged": {
            "employee_pk": matched.get("employee_pk"),
            "matched_name": matched.get("name"),
            "matched_employee_id": matched.get("employee_id"),
            "month": result.get("month"),
            "year": result.get("year"),
            "buckets": result.get("buckets") or {},
            "validation_status": result.get("validation_status"),
            "flags": result.get("flags") or [],
            "summary": result.get("summary"),
            "extraction_status": result.get("status"),
        },
        "source_kind": source_kind,
    }
    tracker.events = (tracker.events or []) + [{
        "stage": PipelineStage.EXTRACTION, "status": "ok",
        "detail": f"AI-extracted {result.get('total_leaves', 0)} leave day(s); awaiting review.",
        "at": _now_iso(),
    }]
    return tracker


async def stage_email_extraction(
    db: AsyncSession, email, *, attachment_ids: list[str], extract_body: bool = False,
) -> list[PipelineFile]:
    """Extract the selected email sources and STAGE them in the pipeline for
    review — without filing a record yet.

    Each source becomes a needs-review PipelineFile carrying the AI-extracted
    leave data (in extraction_meta["staged"]) plus a saved raw copy, so the
    existing Compare & Fix overlay can pre-fill, let the reviewer edit, and
    Accept → file the record + vault (via manual-fix). Rejecting simply leaves
    the file in the pipeline to reprocess / modify / remove later."""
    provider = get_email_provider()
    att_by_id = {a.get("attachment_id"): a for a in (email.attachments or [])}
    msg_id = email.provider_message_id
    staged: list[PipelineFile] = []

    for aid in attachment_ids or []:
        meta = att_by_id.get(aid)
        try:
            data, fn, ct = await provider.get_attachment_bytes(msg_id, aid)
        except FileNotFoundError:
            continue
        staged.append(await _stage_bytes_for_review(
            db,
            filename=fn or (meta.get("filename") if meta else aid) or aid,
            content_type=ct or (meta.get("content_type") if meta else "application/octet-stream"),
            data=data,
            source_kind="email",
            source_id=msg_id,
            attachment_id=aid,
        ))

    if extract_body:
        from app.services.extraction.file_processor import email_body_to_images
        imgs = email_body_to_images(email.subject, email.body_text)
        if imgs:
            staged.append(await _stage_bytes_for_review(
                db, filename="email_body.jpg", content_type="image/jpeg", data=imgs[0],
                source_kind="email", source_id=msg_id, attachment_id=None,
            ))

    await db.commit()
    for t in staged:
        await db.refresh(t)
    return staged


async def stage_upload_extraction(
    db: AsyncSession,
    *,
    files: list[tuple[str, str, bytes]],
) -> list[PipelineFile]:
    """Run Extraction for manual uploads — same staging path as inbox Run Extraction."""
    staged: list[PipelineFile] = []
    for filename, content_type, data in files:
        staged.append(await _stage_bytes_for_review(
            db,
            filename=filename,
            content_type=content_type,
            data=data,
            source_kind="upload",
            source_id=f"upload:{filename}",
            attachment_id=filename,
        ))
    await db.commit()
    for t in staged:
        await db.refresh(t)
    return staged


async def ingest_upload(
    db: AsyncSession, *, filename: str, content_type: str, data: bytes,
) -> tuple[TimesheetRecord | None, PipelineFile]:
    """Direct file-and-record path — used by Agentic Chat store only."""
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
            sp.save_file(account_manager, employee_name, month, year, fn, dat)
        sp.save_text(account_manager, employee_name, month, year, "extraction_result.json", _json.dumps({
            "employee": {"matched_id": matched.employee_id, "matched_name": matched.name,
                         "location": matched.location, "dco_number": matched.dco_number,
                         "account_manager": account_manager, "match_note": "Manual entry."},
            "period": {"month": month, "year": year},
            "leaves": {k: cleaned[k] for k in BUCKET_FIELDS},
            "validation": {"status": validation_status, "summary": summary, "flags": flags},
            "manual": {"note": note}, "approval": approval, "ingested_at": _now_iso(),
        }, indent=2, default=str))
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
    if rec is not None:
        await mark_source_email_ingested(db, tracker)
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
    if rec is not None:
        await mark_source_email_ingested(db, tracker)
    await db.commit()
    if rec is not None:
        await db.refresh(rec)
    await db.refresh(tracker)
    return rec, tracker
