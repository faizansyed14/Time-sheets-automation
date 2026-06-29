"""Coded timesheet detection — no LLM, used as AI-check fallback."""
from __future__ import annotations

import re

from app.services.extraction.file_processor import find_dates_in_text

_ATTENDANCE_FN = re.compile(
    r"attendance|time\s*sheet|timesheet|time_sheet|attendance_shee",
    re.I,
)
_EMP_ID_FN = re.compile(r"\bE\d{6,8}\b", re.I)

_TS_KEYWORDS = (
    "timesheet", "time sheet", "attendance", "time card", "working hours",
    "annual leave", "sick leave", "clock in", "clock out",
)
_NEG_KEYWORDS = (
    "tax invoice", "invoice no", "audit report", "e-signed", "payslip",
    "final audit report", "agreement completed",
)
_APPROVAL_KEYWORDS = (
    "approved", "i approve", "approval", "signed off", "manager approval",
)
_MONTHS = (
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december",
)


def looks_like_timesheet(text: str) -> tuple[bool, str]:
    t = (text or "").lower()
    if len(t.strip()) < 20:
        return False, "almost no readable text"
    score = 0
    if any(k in t for k in _TS_KEYWORDS):
        score += 2
    if any(m in t for m in _MONTHS):
        score += 1
    if len(find_dates_in_text(text)) >= 3:
        score += 1
    if any(n in t for n in _NEG_KEYWORDS):
        score -= 3
    if score >= 2:
        return True, "timesheet signals in text"
    return False, "not a timesheet"


def coded_category(text: str) -> tuple[str, str]:
    ok, reason = looks_like_timesheet(text)
    if ok:
        return "timesheet", reason
    t = (text or "").lower().strip()
    if any(k in t for k in _APPROVAL_KEYWORDS) and len(t) < 600:
        return "approval", "approval phrase"
    return "other", reason


_EMP_ID_RE = re.compile(
    r"(?:emp(?:loyee)?\s*(?:no|id|#|code)?|dco)\s*[:.]?\s*([A-Z]?\d{4,8})",
    re.I,
)


def filename_timesheet_hint(filename: str) -> tuple[str | None, str]:
    """Filename-only hint for Adobe Sign / attendance PDFs."""
    fn = (filename or "").strip()
    if not fn or not _ATTENDANCE_FN.search(fn):
        return None, ""
    low = fn.lower()
    # Adobe Sign audit trail copy of the same attendance doc — not the sheet itself.
    if "audit" in low and "signed" not in low:
        return "other", "adobe sign audit trail filename"
    return "timesheet", "attendance/timesheet filename"


def extract_id_from_filename(filename: str) -> str | None:
    m = _EMP_ID_FN.search(filename or "")
    return m.group(0).upper() if m else None


def extract_identity_from_text(text: str) -> tuple[str | None, str | None]:
    """Best-effort id/name from document text (no LLM)."""
    emp_id = None
    m = _EMP_ID_RE.search(text or "")
    if m:
        emp_id = m.group(1).upper()
    name = None
    for pat in (
        r"employee\s*name\s*[:.]?\s*([A-Za-z][A-Za-z\s.'-]{2,60})",
        r"name\s*[:.]?\s*([A-Za-z][A-Za-z\s.'-]{2,60})",
    ):
        nm = re.search(pat, text or "", re.I)
        if nm:
            name = nm.group(1).strip()
            break
    return emp_id, name
