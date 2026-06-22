"""
Mock extraction engine.

Returns canned-but-coherent results tied to app.seed.mock_data, then runs the
SAME real validation pass (validation.validate) the production engine would, so
the summary / yellow flags are genuinely computed, not faked.

Ad-hoc uploads (files NOT from the seeded emails) are read with a deterministic
text parser. It understands the common real-world layouts — labels like
"Employee Name", "NAME", "EMP NO", "Employee ID", "DCO", and dates written as
ISO (2026-05-01) or "Friday, 1 May 2026" — so the Upload page works end-to-end
in mock mode without an LLM. The real LLM (EXTRACTION_ENGINE=vision) replaces
this seamlessly and additionally reads scanned/image timesheets.
"""
from __future__ import annotations

import calendar
import re
from collections import Counter

from app.seed import mock_data
from app.services.extraction import file_processor as fp
from app.services.extraction.base import (
    ApprovalExtraction,
    ExtractionEngine,
    TimesheetExtraction,
)
from app.services.extraction.validation import (
    unaccounted_flag,
    unaccounted_working_days,
    validate,
)

_MONTH_NUM = fp._MONTH_NUM

_BUCKET_KEYWORDS = [
    ("public_holiday", ("public holiday", "(ph)", "holiday")),
    ("remote", ("work from home", "wfh", "work remotely", "remote", "grp")),
    ("sick", ("sick leave", "sick", "medical leave", "(sl)")),
    ("unpaid", ("unpaid", "lop", "leave without pay", "(ul)")),
    ("absent", ("absent", "unauthorized", "no time", "absence")),
    ("annual", ("annual leave", "annual", "(al)", "paid leave", "vacation", "comp off")),
]

_NAME_LABELS = [
    r"Employee\s*Name", r"Emp\s*Name", r"Full\s*Name", r"Staff\s*Name", r"Name",
]
_ID_LABELS = [
    r"Employee\s*ID", r"Employee\s*Code", r"Employee\s*No\.?", r"Emp\s*No\.?",
    r"EMP\s*NO\.?", r"Emp\s*ID", r"Staff\s*ID", r"DCO", r"SMC",
]


def _field(text: str, labels: list[str], value_re: str) -> str:
    for lab in labels:
        m = re.search(rf"(?im)^\s*{lab}\s*[:\-]\s*({value_re})\s*$", text)
        if not m:
            m = re.search(rf"(?i){lab}\s*[:\-]\s*({value_re})", text)
        if m:
            v = m.group(1).strip()
            if v and "(not printed)" not in v.lower():
                return v
    return ""


def _parse_upload_text(text: str) -> dict | None:
    """Deterministic 'LLM': read identity, period and leave rows from plain text."""
    if not text or not text.strip():
        return None

    name = _field(text, _NAME_LABELS, r"[A-Za-z][A-Za-z .'\-]{1,60}")
    emp_id = _field(text, _ID_LABELS, r"[A-Za-z]{0,5}-?\d[\w\-/]*")

    # explicit header month, e.g. "Month: May 2026" or "Timesheet - May 2026"
    month = year = 0
    mh = re.search(r"(?i)(?:month|period|timesheet)\s*[:\-]?\s*([A-Za-z]{3,9})[\s,]+(\d{4})", text)
    if mh and mh.group(1).lower() in _MONTH_NUM:
        month, year = _MONTH_NUM[mh.group(1).lower()], int(mh.group(2))

    dates = fp.find_dates_in_text(text)

    # period: prefer the explicit header, else the dominant month/year of ALL
    # dates on the sheet (working days included) — robust to sparse leave rows.
    if not (month and year) and dates:
        (py, pm), _ = Counter((int(iso[:4]), int(iso[5:7])) for _, _, iso in dates).most_common(1)[0]
        month, year = pm, py

    buckets: dict[str, list[str]] = {b: [] for b, _ in _BUCKET_KEYWORDS}
    for i, (s, e, iso) in enumerate(dates):
        seg_end = dates[i + 1][0] if i + 1 < len(dates) else min(len(text), e + 60)
        segment = text[e:seg_end].lower()
        for bucket, words in _BUCKET_KEYWORDS:
            if any(w in segment for w in words):
                buckets[bucket].append(iso)
                break  # a leave date belongs to exactly one bucket

    if not (name or emp_id) and not (month and year):
        return None
    present, weekend = fp.scan_attendance_grid(text)
    return {"emp_id": emp_id, "emp_name": name, "month": month, "year": year,
            "_present": sorted(present), "_weekend": sorted(weekend), **buckets}


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

        # Daily-grid sheets: flag weekdays that have neither hours nor a leave.
        present = set(case.get("_present") or [])
        if len(present) >= 5:
            accounted = {d for v in cleaned.values() for d in v}
            gaps = unaccounted_working_days(
                case["month"], case["year"], present,
                set(case.get("_weekend") or []), accounted)
            uf = unaccounted_flag(gaps)
            if uf:
                flags = flags + [uf]

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
