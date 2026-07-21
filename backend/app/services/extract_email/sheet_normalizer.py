"""Normalise vision/engine output per sheet."""
from __future__ import annotations

from app.services.extract_email.constants import (
    BUCKETS,
    EMP_ID_IN_TEXT,
    FINANCIAL_DOC_RE,
    LEAVE_CERT_FNAME_RE,
    SUBJECT_TS_RE,
    SYNTHETIC_SHEET_NAME_RE,
    TIMESHEET_FNAME_RE,
)
from app.services.extract_email.types import SheetUnit

def clean_dates(vals, month, year) -> list[str]:
    """Normalise model dates to ISO. ISO-first (the prompt asks for it — the
    shared DMY parser mis-reads YYYY-MM-DD), then DMY wording, then bare day
    numbers resolved against the sheet's own month/year."""
    import datetime as dt
    from app.services.extraction.parser import _parse_one_leave_date
    out = set()
    for v in vals or []:
        s = str(v).strip()
        if not s:
            continue
        d = None
        try:
            d = dt.date.fromisoformat(s[:10])
        except ValueError:
            d = _parse_one_leave_date(s, month, year)
            if d is None and s.isdigit() and month and year:
                try:
                    d = dt.date(year, month, int(s))
                except ValueError:
                    d = None
        if d:
            out.add(d.isoformat())
    return sorted(out)


def as_month(v) -> int | None:
    """1-12 from an int, a numeric string, or a month name/abbr ("May", "Sep").
    Models differ: some return month as an integer, others echo the sheet's
    wording — both must land on the same value."""
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v if 1 <= v <= 12 else None
    s = str(v or "").strip().lower()
    if not s:
        return None
    if s.isdigit():
        i = int(s)
        return i if 1 <= i <= 12 else None
    import calendar
    for i in range(1, 13):
        if s == calendar.month_name[i].lower() or s[:3] == calendar.month_abbr[i].lower():
            return i
    return None


def as_year(v) -> int | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v if v >= 2000 else None
    s = str(v or "").strip()
    return int(s) if s.isdigit() and int(s) >= 2000 else None


def month_token(tok: str) -> int | None:
    import calendar
    s = (tok or "").strip().lower()
    if not s:
        return None
    if s.isdigit():
        m = int(s)
        return m if 1 <= m <= 12 else None
    for i in range(1, 13):
        if s == calendar.month_name[i].lower() or s[:3] == calendar.month_abbr[i].lower():
            return i
    return None



def infer_from_filename(filename: str, subject: str | None = None) -> dict:
    """Best-effort kind / identity / period from attachment name (+ subject)."""
    name = (filename or "").strip()
    if not name or name == "(email body)" or SYNTHETIC_SHEET_NAME_RE.match(name):
        return {}
    low = name.lower()
    out: dict = {}

    m = TIMESHEET_FNAME_RE.search(name)
    if m:
        out["kind"] = "timesheet"
        if month := month_token(m.group("month")):
            out["month"] = month
        if year := as_year(m.group("year")):
            out["year"] = year
        tail = (m.group("tail") or "").strip()
        if tail:
            if id_m := EMP_ID_IN_TEXT.search(tail):
                out["employee_id"] = id_m.group(1).upper()
                name_part = tail[:id_m.start()].strip(" _-")
                if name_part and not EMP_ID_IN_TEXT.fullmatch(name_part):
                    out["employee_name"] = name_part.replace("_", " ").title()
    elif LEAVE_CERT_FNAME_RE.search(low):
        out["kind"] = "leave_certificate"
        for i in range(1, 13):
            import calendar
            for label in (calendar.month_name[i].lower(), calendar.month_abbr[i].lower()):
                if len(label) >= 3 and label in low:
                    out["month"] = i
                    break
            if out.get("month"):
                break
    elif "timesheet" in low:
        out["kind"] = "timesheet"
        if id_m := EMP_ID_IN_TEXT.search(name):
            out["employee_id"] = id_m.group(1).upper()

    subj = subject or ""
    if subj_m := SUBJECT_TS_RE.search(subj):
        out.setdefault("kind", "timesheet")
        out.setdefault("month", month_token(subj_m.group("month")))
        out.setdefault("year", as_year(subj_m.group("year")))
        out.setdefault("employee_name", subj_m.group("name").strip())
        out.setdefault("employee_id", subj_m.group("id").upper())

    return {k: v for k, v in out.items() if v is not None}


def boost_sheet_from_hints(sheet: dict, unit: SheetUnit, subject: str | None) -> dict:
    hints = infer_from_filename(unit.name, subject)
    if not hints:
        return sheet
    out = dict(sheet)
    if out.get("kind") == "other" and hints.get("kind"):
        out["kind"] = hints["kind"]
    for key in ("employee_name", "employee_id", "month", "year"):
        if not out.get(key) and hints.get(key):
            out[key] = hints[key]
    return out


def sanitize_body_sheet(sheet: dict, unit: SheetUnit) -> dict:
    """Stop quoted Subject lines / logos / approval replies from staging as
    empty timesheets.

    Applies only to sheets with NO real name to trust — the body text itself,
    or a placeholder we invented for an unlabeled inline image/attachment
    (e.g. "body_timesheet.png" — could just as easily be a signature logo).
    A REAL attachment filename (e.g. "June_Timesheet.pdf") is trusted even
    when it reads as a clean/empty grid — that is legitimate zero-leave data.
    For ambiguous/unlabeled sheets, kind=timesheet only sticks when leave
    dates or an actual text grid back it up; otherwise it demotes to "other"
    so a logo/signature/quoted-subject-line can't stage as a blank record.
    """
    if (unit.name != "(email body)" and not SYNTHETIC_SHEET_NAME_RE.match(unit.name)) \
            or sheet.get("kind") != "timesheet":
        return sheet
    buckets = sheet.get("buckets") or {}
    if any(buckets.get(b) for b in BUCKETS):
        return sheet
    from app.services.extraction.file_processor import find_dates_in_text, scan_attendance_grid

    present, _ = scan_attendance_grid(unit.text or "")
    if len(present) >= 5:
        return sheet
    if len(find_dates_in_text(unit.text or "")) >= 5:
        return sheet
    out = dict(sheet)
    out["kind"] = "other"
    out["month"] = None
    out["year"] = None
    out["employee_name"] = None
    out["employee_id"] = None
    return out


def demote_financial_sheet(sheet: dict, unit: SheetUnit) -> dict:
    """A model can misread an invoice/ticket/receipt's tabular row layout as a
    leave grid (measured: a travel-agency ticket invoice was staged as an
    empty timesheet — "S.# Description Amount", "PNR", "Sub Total", "VAT").
    Deterministic safety net, independent of any one model's judgement: when
    the sheet's OWN text carries clear invoice/billing/travel-booking markers
    and no real leave data backs the "timesheet" call, force it to "other"."""
    if sheet.get("kind") not in ("timesheet", "leave_certificate"):
        return sheet
    buckets = sheet.get("buckets") or {}
    if any(buckets.get(b) for b in BUCKETS):
        return sheet  # real leave data was read — trust it, don't second-guess
    if not FINANCIAL_DOC_RE.search(unit.text or ""):
        return sheet
    out = dict(sheet)
    out["kind"] = "other"
    out["month"] = None
    out["year"] = None
    out["employee_name"] = None
    out["employee_id"] = None
    return out


def normalize_sheet(unit: SheetUnit, raw: dict) -> dict:
    kind = str(raw.get("kind") or "other").lower()
    if kind not in ("timesheet", "leave_certificate", "approval", "other"):
        kind = "other"
    # Prefer classifier kind for approval / leave_certificate when extract said other.
    clf = getattr(unit, "classify", None)
    if clf is not None and getattr(clf, "kind", None) in ("approval", "leave_certificate"):
        if kind == "other":
            kind = clf.kind
    month = as_month(raw.get("month")) or (getattr(clf, "month", None) if clf else None)
    year = as_year(raw.get("year")) or (getattr(clf, "year", None) if clf else None)
    dates_complete = True
    missing_days: list[int] = []
    classify_confidence = None
    if clf is not None:
        dates_complete = bool(getattr(clf, "dates_complete", True))
        missing_days = list(getattr(clf, "missing_days", None) or [])
        classify_confidence = getattr(clf, "confidence", None)
    incomplete_sheet = (kind == "timesheet" and not dates_complete)
    return {
        "name": unit.name,
        "kind": kind,
        "employee_name": (str(raw.get("employee_name")).strip() or None)
        if raw.get("employee_name") else None,
        "employee_id": (str(raw.get("employee_id")).strip() or None)
        if raw.get("employee_id") else None,
        "month": month,
        "year": year,
        "buckets": {b: clean_dates(raw.get(b), month, year) for b in BUCKETS},
        "manager_signature": bool(raw.get("manager_signature")),
        "approval_evidence": str(raw.get("approval_evidence") or "")[:200],
        "format_id": getattr(unit, "format_id", "generic"),
        "text": (unit.text or "")[:12000],
        "dates_complete": dates_complete,
        "incomplete_sheet": incomplete_sheet,
        "missing_days": missing_days,
        "classify_confidence": classify_confidence,
        "expected_day_count": getattr(clf, "expected_day_count", 0) if clf else 0,
        "observed_day_count": getattr(clf, "observed_day_count", 0) if clf else 0,
    }
