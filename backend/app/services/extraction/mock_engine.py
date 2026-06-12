"""
Mock extraction engine.

Returns canned-but-coherent results tied to app.seed.mock_data, then runs the
SAME real validation pass (validation.validate) the production engine would, so
the summary / yellow flags are genuinely computed, not faked.

Ad-hoc uploads (files NOT from the seeded emails) are read with a deterministic
text parser: it pulls "Employee Name:", "Employee ID:", "Month:" and the
date/status rows out of the document text — so the Upload page works
end-to-end in mock mode with any file that follows the demo timesheet layout.
The real LLM (EXTRACTION_ENGINE=vision) replaces this seamlessly.
"""
from __future__ import annotations

import calendar
import re

from app.seed import mock_data
from app.services.extraction.base import (
    ApprovalExtraction,
    ExtractionEngine,
    TimesheetExtraction,
)
from app.services.extraction.validation import validate

_MONTH_NUM = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
_MONTH_NUM.update({m.lower(): i for i, m in enumerate(calendar.month_abbr) if m})

_BUCKET_KEYWORDS = [
    ("public_holiday", ("public holiday", "(ph)")),
    ("remote", ("work from home", "wfh", "remote")),
    ("sick", ("sick",)),
    ("unpaid", ("unpaid", "lop")),
    ("absent", ("absent",)),
    ("annual", ("annual", "(al)", "paid leave")),
]

_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _parse_upload_text(text: str) -> dict | None:
    """Deterministic 'LLM': read identity, month and date rows from plain text."""
    if not text or not text.strip():
        return None

    def _field(pattern: str) -> str:
        m = re.search(pattern, text, re.IGNORECASE)
        v = (m.group(1).strip() if m else "")
        return "" if "(not printed)" in v.lower() else v

    name = _field(r"Employee\s*Name\s*[:\-]\s*(.+)")
    emp_id = _field(r"Employee\s*ID\s*[:\-]\s*([A-Za-z0-9\-\/]+)")
    month = year = 0
    m = re.search(r"Month\s*[:\-]\s*([A-Za-z]+)[\s,]+(\d{4})", text, re.IGNORECASE)
    if m and m.group(1).lower() in _MONTH_NUM:
        month, year = _MONTH_NUM[m.group(1).lower()], int(m.group(2))

    buckets: dict[str, list[str]] = {b: [] for b, _ in _BUCKET_KEYWORDS}
    # Classify each date by the text between it and the next date.
    hits = list(_DATE_RE.finditer(text))
    for i, h in enumerate(hits):
        seg_end = hits[i + 1].start() if i + 1 < len(hits) else min(len(text), h.end() + 80)
        segment = text[h.end():seg_end].lower()
        for bucket, words in _BUCKET_KEYWORDS:
            if any(w in segment for w in words):
                buckets[bucket].append(h.group(0))
                break
        # infer month/year from the dates if the header didn't say
        if not month:
            year, month = int(h.group(0)[:4]), int(h.group(0)[5:7])

    if not (name or emp_id) and not month:
        return None
    return {"emp_id": emp_id, "emp_name": name, "month": month, "year": year, **buckets}


class MockExtractionEngine(ExtractionEngine):
    async def extract_timesheet(
        self, data: bytes, filename: str, content_type: str,
        message_id: str, attachment_id: str,
    ) -> TimesheetExtraction:
        case = mock_data.case_for_attachment(attachment_id)
        if not case:
            # Ad-hoc upload: parse the document text deterministically.
            from app.services.extraction.file_processor import detect_file_type, extract_document_text
            ftype = detect_file_type(filename, data)
            text = extract_document_text(ftype, data) if ftype != "unknown" else ""
            parsed = _parse_upload_text(text)
            if not parsed:
                return TimesheetExtraction(
                    employee_id=None, employee_name=None, month=0, year=0,
                    validation_status="manual_review",
                    summary="Could not read an employee name, ID or month from this file "
                            "(mock engine reads text only — set EXTRACTION_ENGINE=vision "
                            "for scanned/image timesheets).",
                    hr_flags=["Upload not readable by the mock engine."],
                )
            case = parsed

        raw = {
            "annual": case.get("annual", []),
            "remote": case.get("remote", []),
            "sick": case.get("sick", []),
            "unpaid": case.get("unpaid", []),
            "absent": case.get("absent", []),
            "public_holiday": case.get("public_holiday", []),
        }
        cleaned, flags = validate(
            raw, case["month"], case["year"],
            header_month=case.get("header_month"), header_year=case.get("header_year"),
        )

        status = "manual_review" if flags else "verified"
        if flags:
            summary = "Needs review: " + " ".join(flags)
        else:
            total = sum(len(v) for v in cleaned.values())
            mname = calendar.month_name[case["month"]]
            summary = f"Clean extraction — {total} leave/holiday day(s) for {mname} {case['year']}."

        return TimesheetExtraction(
            employee_id=case.get("emp_id") or None,
            employee_name=case.get("emp_name"),
            month=case["month"],
            year=case["year"],
            annual_leave_dates=cleaned["annual"],
            remote_work_dates=cleaned["remote"],
            sick_leave_dates=cleaned["sick"],
            unpaid_leave_dates=cleaned["unpaid"],
            absent_dates=cleaned["absent"],
            public_holiday_dates=cleaned["public_holiday"],
            validation_status=status,
            summary=summary,
            hr_flags=flags,
        )

    async def extract_approval(
        self, data: bytes, message_id: str, attachment_id: str,
    ) -> ApprovalExtraction:
        ap = mock_data.approval_for_message(message_id)
        if not ap:
            return ApprovalExtraction(detected=False, detail="No approval screenshot in this email.")
        return ApprovalExtraction(detected=bool(ap["detected"]), detail=ap["detail"])
