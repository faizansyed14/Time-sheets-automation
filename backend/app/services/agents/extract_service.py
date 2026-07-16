"""
Extract leaves from a chat-uploaded sheet — grounded, not hallucinated.

The chat assistant does NOT read the raw file itself. The file runs through
the SAME analysis pipeline as Extract Email and the Upload page
(full_email_extract.analyse_upload: images + text + OCR → vision model →
deterministic grouping/validation), and the chat is handed the structured
result. The numbers and dates come from that validated pipeline, not from the
language model, so there is nothing for the chat to invent.

Nothing is persisted here: no PipelineFile, no TimesheetRecord, no storage
write. Storing (which stages the file for Compare & Fix review) is a separate,
explicit user action.
"""
from __future__ import annotations

import calendar
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.employee import Employee
from app.services.agents import full_email_extract as fx
from app.services.extraction.file_processor import detect_file_type
from app.services.extraction.validation import summarize as _summarize
from app.services.extraction.validation import validate as _validate

_SUPPORTED = {"pdf", "docx", "xlsx", "image", "eml"}

_BUCKET_LABELS = {
    "annual": "Annual leave",
    "remote": "Remote work (WFH)",
    "sick": "Sick leave",
    "maternity": "Maternity leave",
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
    """Run the shared extraction pipeline on uploaded bytes (no persistence).

    Returns a structured dict the chat surfaces verbatim:
      {status, filename, buckets, matched_employee, month, year,
       leaves:{label:[dates]}, counts:{label:int}, total_leaves,
       validation_status, flags, summary}
    """
    if not data:
        return {"status": "empty_file", "filename": filename}

    ftype = detect_file_type(filename, data)
    if ftype not in _SUPPORTED:
        return {"status": "unsupported_type", "filename": filename, "detected": ftype,
                "message": "Accepted: PDF, DOCX, XLSX, EML."}

    try:
        res = await fx.analyse_upload(db, filename=filename, data=data)
    except Exception as e:
        return {"status": "extraction_failed", "filename": filename, "error": str(e)[:300]}

    groups = res["groups"]
    if not groups:
        kinds = ", ".join(f"{s['name']} ({s['kind']})" for s in res["sheets"])
        return {"status": "extraction_failed", "filename": filename,
                "error": f"No timesheet or certificate found ({kinds or 'nothing readable'})."}

    # The chat previews ONE result — pick the group with the most leave days.
    primary = max(groups, key=lambda g: sum(len(v) for v in g["buckets"].values()))
    month, year = primary["month"], primary["year"]
    flags = list(primary["overlap_flags"]) + list(primary["fold_notes"])
    if len(groups) > 1:
        flags.append(
            f"This file contains {len(groups)} employee/month group(s) — showing "
            f"{primary['name'] or 'the largest one'}. Storing it stages EVERY group for review.")

    has_period = bool(month and year)
    if has_period:
        cleaned, val_flags = _validate(primary["buckets"], month, year)
        flags += val_flags
        summary = _summarize(cleaned, flags, month, year, len(primary["sheets"]))
    else:
        cleaned = primary["buckets"]
        flags.append("No usable month/year found on the sheet.")
        summary = "Could not read a month/year from this sheet."

    matched = None
    if primary["employee_pk"]:
        emp = (await db.execute(select(Employee).where(
            Employee.id == primary["employee_pk"]))).scalar_one_or_none()
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
        "extracted_employee_name": primary["name"],
        "extracted_employee_id": primary["employee_id"],
        "matched_employee": matched,
        "match_note": primary["note"],
        "month": month if has_period else None,
        "year": year if has_period else None,
        "month_name": calendar.month_name[month] if has_period else None,
        "leaves": leaves,
        "counts": counts,
        "total_leaves": sum(counts.values()),
        "validation_status": "manual_review" if flags else "verified",
        "flags": flags,
        "summary": f"{summary} {res['approval']['detail']}",
        "extraction_method": res["run_meta"]["method"],
        "used_ocr": False,
    }
