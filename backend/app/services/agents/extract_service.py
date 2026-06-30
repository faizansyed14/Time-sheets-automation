"""
Extract leaves from a chat-uploaded sheet — grounded, not hallucinated.

The chat assistant does NOT read the raw file itself. Instead we run the file
through the *same* extraction engine + validation + employee matching the
Upload and email-Accept flows use, then hand the chat a structured result. The
numbers and dates therefore come from the deterministic/validated pipeline, not
from the language model, so there is nothing for the chat to invent.

Nothing is persisted: no TimesheetRecord, no storage-provider write. The result
is purely informational until the user explicitly asks to update an employee.
"""
from __future__ import annotations

import calendar
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.extraction import get_extraction_engine
from app.services.extraction.file_processor import detect_file_type
from app.services.extraction.validation import summarize as _summarize
from app.services.extraction.validation import validate as _validate
from app.services.pipeline import matching

_SUPPORTED = {"pdf", "docx", "xlsx", "image", "eml"}

_BUCKET_LABELS = {
    "annual": "Annual leave",
    "remote": "Remote work (WFH)",
    "sick": "Sick leave",
    "unpaid": "Unpaid leave (LOP)",
    "absent": "Absent",
    "public_holiday": "Public holiday",
}


def _employee_email(emp) -> str | None:
    if not emp:
        return None
    primary = (getattr(emp, "employee_email_id", None) or "").strip()
    if primary:
        return primary
    allm = (getattr(emp, "all_emails", None) or "").strip()
    return allm.split(";")[0].strip() if allm else None


async def extract_from_upload(
    db: AsyncSession, *, filename: str, content_type: str, data: bytes,
) -> dict[str, Any]:
    """Run the real extraction pipeline on uploaded bytes (no persistence).

    Returns a structured dict the chat surfaces verbatim:
      {status, filename, employee:{...}, month, year, leaves:{label:[dates]},
       counts:{label:int}, total_leaves, validation_status, flags, summary}
    """
    if not data:
        return {"status": "empty_file", "filename": filename}

    ftype = detect_file_type(filename, data)
    if ftype not in _SUPPORTED:
        return {"status": "unsupported_type", "filename": filename, "detected": ftype,
                "message": "Accepted: PDF, DOCX, XLSX, EML."}

    engine = get_extraction_engine()
    try:
        ext = await engine.extract_timesheet(
            data, filename, content_type, "chat-upload", filename)
    except Exception as e:
        return {"status": "extraction_failed", "filename": filename, "error": str(e)[:300]}

    # Validate/clean exactly as the pipeline would (dedupe, out-of-month flags…).
    raw_buckets = {
        "annual": ext.annual_leave_dates or [], "remote": ext.remote_work_dates or [],
        "sick": ext.sick_leave_dates or [], "unpaid": ext.unpaid_leave_dates or [],
        "absent": ext.absent_dates or [], "public_holiday": ext.public_holiday_dates or [],
    }
    has_period = bool(1 <= (ext.month or 0) <= 12 and (ext.year or 0) >= 2000)
    if has_period:
        cleaned, flags = _validate(raw_buckets, ext.month, ext.year)
        summary = _summarize(cleaned, flags, ext.month, ext.year)
    else:
        cleaned, flags = raw_buckets, ["No usable month/year found on the sheet."]
        summary = ext.summary or "Could not read a month/year from this sheet."

    # Match to an employee in the matcher (id + name), so the chat can act on it.
    match = await matching.match_employee(db, ext.employee_id, ext.employee_name)
    emp = match.employee
    matched = None
    if emp:
        matched = {
            "employee_pk": emp.id,
            "employee_id": emp.employee_id,
            "name": emp.name,
            "email": _employee_email(emp),
            "location": emp.location,
        }

    leaves = {_BUCKET_LABELS[k]: cleaned.get(k, []) for k in _BUCKET_LABELS}
    counts = {label: len(dates) for label, dates in leaves.items()}

    return {
        "status": "ok",
        "filename": filename,
        # Raw bucket-keyed dates (annual/remote/sick/…) for the pipeline + edit UI.
        "buckets": {k: cleaned.get(k, []) for k in _BUCKET_LABELS},
        "extracted_employee_name": ext.employee_name,
        "extracted_employee_id": ext.employee_id,
        "matched_employee": matched,
        "match_note": match.note,
        "month": ext.month if has_period else None,
        "year": ext.year if has_period else None,
        "month_name": calendar.month_name[ext.month] if has_period else None,
        "leaves": leaves,
        "counts": counts,
        "total_leaves": sum(counts.values()),
        "validation_status": "manual_review" if flags else "verified",
        "flags": flags,
        "summary": summary,
        "extraction_method": getattr(ext, "extraction_method", None),
        "used_ocr": bool(getattr(ext, "used_ocr", False)),
    }
