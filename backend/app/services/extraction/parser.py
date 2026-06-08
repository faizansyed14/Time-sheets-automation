"""
Ported from your project's `parser.py` — the REAL extraction + validation prompts
and JSON parsing. Kept verbatim where it matters so behaviour matches production.

If you want byte-identical behaviour, paste your current parser.py over this file;
the function names used by vision_engine.py are: SYSTEM_PROMPT, EXTRACTION_PROMPT,
TEXT_EXTRACTION_SYSTEM, build_text_extraction_prompt, extract_json_from_vllm_response,
parse_extraction, parse_text_extraction, validate_dates_in_month.
"""
from __future__ import annotations

import calendar
import datetime as dt
import json
from typing import Any

from pydantic import BaseModel, Field


class LeaveOccurrence(BaseModel):
    date: dt.date
    day_of_week: str | None = None
    notes: str | None = None


class ExtractedTimesheet(BaseModel):
    employee_full_name: str | None = None
    employee_id: str | None = None
    client_name: str | None = None
    month: int | None = None
    year: int | None = None
    total_calendar_days_in_month: int | None = None

    annual_leave_dates: list[LeaveOccurrence] = Field(default_factory=list)
    work_from_home_dates: list[LeaveOccurrence] = Field(default_factory=list)
    paid_leave_dates: list[LeaveOccurrence] = Field(default_factory=list)
    sick_leave_dates: list[LeaveOccurrence] = Field(default_factory=list)
    public_holidays_dates: list[LeaveOccurrence] = Field(default_factory=list)
    unpaid_leave_dates: list[LeaveOccurrence] = Field(default_factory=list)
    absent_dates: list[LeaveOccurrence] = Field(default_factory=list)
    weekly_off_dates: list[LeaveOccurrence] = Field(default_factory=list)
    other_leave_dates: list[LeaveOccurrence] = Field(default_factory=list)

    overall_confidence: str | None = None
    uncertain_fields: list[str] = Field(default_factory=list)


class TextExtraction(BaseModel):
    annual_dates: list[str] = Field(default_factory=list)
    work_from_home_dates: list[str] = Field(default_factory=list)
    sick_dates: list[str] = Field(default_factory=list)
    public_holiday_dates: list[str] = Field(default_factory=list)
    unpaid_dates: list[str] = Field(default_factory=list)
    absent_dates: list[str] = Field(default_factory=list)


SYSTEM_PROMPT = """You are a precise and careful timesheet extraction engine.
Return ONLY valid JSON. Do not output markdown.
Use careful internal reasoning to identify the correct columns and extract every leave row.
Never hallucinate: if a leave type/date is not clearly present, leave the corresponding list empty.
Always ensure counts match the date arrays.
Do not infer or output attendance summary numbers (days present, weekend counts, total working days); only extract leave buckets and dates."""


EXTRACTION_PROMPT = """Extract timesheet data and return ONLY the JSON below. No markdown. No explanation.
MANDATORY FIELDS — these must NEVER be empty:

employee_name — Look for labels: "Employee Name", "Name", "Employee"
employee_id — Look for: "EMP NO", "Emp No.", "Employee ID", "Employee Code", "SMC-", or any ID/code near the name

═══════════════════════════════════════
LEAVE EXTRACTION RULES — READ CAREFULLY
═══════════════════════════════════════
Scan EVERY row from first to last. For each row:

Find the DATE in that row
Find the STATUS in that row — check columns named: Status, Remarks, Timesheet Status, Leave Type, Attendance Type, Attendance Sub Type, Sub Type
Match the status to exactly ONE category below and add the date to that list only

═══════════════════════════════════════
CATEGORY KEYWORDS
═══════════════════════════════════════
ALWAYS use the most specific subtype column available (Attendance Sub Type > Attendance Type > Status).

public_holidays
→ Public Holiday, PH, Holiday, Public
annual_leaves
→ Annual Leave, Annual Leave (Approved), AL, PL, Paid Leave, Vacation, Comp Off
⚠ DO NOT put WFH, Remote, GRP, or Work Remotely here — those go in work_from_home
work_from_home
→ WFH, Work From Home, Work Remotely, Remote Work, Remote, Temporary Remote, GRP, GRP Temporary Work Remotely
⚠ These must NEVER appear in annual_leaves
sick_leaves
→ Sick Leave, SL, Medical Leave, Sick
unpaid_leaves
→ Unpaid Leave, UL, LOP, Leave Without Pay
unauthorized_absences
→ Absent, Unauthorized, No Time, Absence

═══════════════════════════════════════
CRITICAL RULES
═══════════════════════════════════════

SUB TYPE ALWAYS WINS — If both Attendance Type and Attendance Sub Type columns exist, classify using Sub Type only. Ignore the generic Attendance Type in that case.
WFH / REMOTE / GRP → ALWAYS work_from_home. NEVER annual_leaves. No exceptions.
Do NOT skip the first rows of the month — dates like 1st, 2nd of the month are often missed. Include every single one.
Do NOT skip any page of the document.
One row = one date. Do not mix dates across rows.
Weekends and Rest Days → ignore completely. Do not add to any list.
Each count must always equal the exact number of dates in its list.
Every leave, absence, or WFH row must appear in exactly one list — no duplicates, no omissions.

═══════════════════════════════════════
OUTPUT JSON
═══════════════════════════════════════
{
"employee_name": "",
"employee_id": "",
"client": "",
"month": "",
"year": "",
"public_holidays":          {"count": 0, "dates": []},
"annual_leaves":            {"count": 0, "dates": []},
"work_from_home":           {"count": 0, "dates": []},
"sick_leaves":              {"count": 0, "dates": []},
"unpaid_leaves":            {"count": 0, "dates": []},
"unauthorized_absences":    {"count": 0, "dates": []},
"confidence": "High"
}
Date format: DD-Mon-YYYY (example: 02-Jan-2026). Be consistent across all dates."""


TEXT_EXTRACTION_SYSTEM = """You read FULL document text from a timesheet and return ONLY JSON with leave dates per type.
Convert all dates to ISO YYYY-MM-DD. Ignore P/Present, weekends/rest days, and non-leave markers.
Never hallucinate."""

TEXT_EXTRACTION_PROMPT = """From the document text below, return ONLY this JSON (no markdown):
{
  "annual_dates": [], "work_from_home_dates": [], "sick_dates": [],
  "public_holiday_dates": [], "unpaid_dates": [], "absent_dates": []
}
Rules: ISO YYYY-MM-DD; classify by leave codes/keywords; ignore weekends/Present.

Document text:
---
{document_text}
---"""

TEXT_EXTRACT_DOC_MAX_LEN = 500_000


def build_text_extraction_prompt(document_text: str) -> str:
    doc = (document_text or "").strip()
    if len(doc) > TEXT_EXTRACT_DOC_MAX_LEN:
        doc = doc[:TEXT_EXTRACT_DOC_MAX_LEN] + "\n\n[... document truncated for length ...]"
    return TEXT_EXTRACTION_PROMPT.replace("{document_text}", doc)


_MONTH_NAMES = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10,
    "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}


def _parse_one_leave_date(s: str, month: int | None, year: int | None) -> dt.date | None:
    if not s or not isinstance(s, str):
        return None
    s = s.strip()
    if not s:
        return None
    norm = s.replace(".", "-").replace("/", "-").replace(" ", "-")
    parts = [p for p in norm.split("-") if p]
    try:
        if len(parts) == 3:
            d, mo, y = parts
            day = int(d)
            mo_l = mo.lower()
            mon = _MONTH_NAMES.get(mo_l[:3], None) or (int(mo) if mo.isdigit() else None)
            yr = int(y) if len(y) == 4 else (2000 + int(y))
            if mon:
                return dt.date(yr, mon, day)
        # try ISO
        return dt.date.fromisoformat(s)
    except Exception:
        return None


def _collect_text(raw: Any) -> str:
    """Pull the model's text out of any supported response shape."""
    if isinstance(raw, str):
        return raw
    if not isinstance(raw, dict):
        return ""

    # 1) Chat Completions: choices[0].message.content (str OR list of parts)
    try:
        c = raw["choices"][0]["message"]["content"]
        if isinstance(c, str) and c.strip():
            return c
        if isinstance(c, list):
            parts = [p.get("text", "") for p in c if isinstance(p, dict)]
            if any(parts):
                return "".join(parts)
    except Exception:
        pass

    # 2) Responses API convenience field
    ot = raw.get("output_text")
    if isinstance(ot, str) and ot.strip():
        return ot

    # 3) Responses API full structure: output[].content[].text
    try:
        texts: list[str] = []
        for item in raw.get("output", []) or []:
            for part in item.get("content", []) or []:
                if isinstance(part, dict) and part.get("text"):
                    texts.append(part["text"])
        if texts:
            return "".join(texts)
    except Exception:
        pass

    return ""


def extract_json_from_vllm_response(raw: dict[str, Any]) -> dict[str, Any]:
    # Already a parsed extraction object?
    if isinstance(raw, dict) and ("employee_name" in raw or "annual_leaves" in raw):
        return raw

    text = _collect_text(raw).strip().replace("```json", "").replace("```", "").strip()
    if not text:
        raise ValueError("LLM returned an empty response (no text content to parse).")

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Model wrapped JSON in prose — grab the first {...} block.
        import re
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            return json.loads(m.group(0))
        raise ValueError(f"LLM response was not valid JSON. First 200 chars: {text[:200]}")


def _occ_list(values: Any, month: int | None, year: int | None) -> list[LeaveOccurrence]:
    if not values:
        return []
    items = values if isinstance(values, list) else [values]
    out: list[LeaveOccurrence] = []
    for it in items:
        s = str(it).strip()
        d = _parse_one_leave_date(s, month, year)
        if d:
            out.append(LeaveOccurrence(date=d, day_of_week=d.strftime("%A")))
    return out


def _bucket(data: dict, *keys) -> Any:
    for k in keys:
        if k in data:
            v = data[k]
            if isinstance(v, dict) and "dates" in v:
                return v.get("dates")
            return v
    return []


def parse_extraction(raw_json: dict[str, Any]) -> ExtractedTimesheet:
    data = raw_json or {}
    month = data.get("month")
    year = data.get("year")
    if isinstance(month, str):
        month = _MONTH_NAMES.get(month.strip().lower()[:3], None) or (int(month) if month.strip().isdigit() else None)
    if isinstance(year, str) and year.strip().isdigit():
        year = int(year)

    return ExtractedTimesheet(
        employee_full_name=(data.get("employee_name") or data.get("employee_full_name") or None),
        employee_id=(data.get("employee_id") or None),
        client_name=(data.get("client") or data.get("client_name") or None),
        month=month, year=year,
        annual_leave_dates=_occ_list(_bucket(data, "annual_leaves", "annual_leave_dates"), month, year),
        work_from_home_dates=_occ_list(_bucket(data, "work_from_home", "work_from_home_dates"), month, year),
        paid_leave_dates=_occ_list(_bucket(data, "paid_leaves", "paid_leave_dates"), month, year),
        sick_leave_dates=_occ_list(_bucket(data, "sick_leaves", "sick_leave_dates"), month, year),
        public_holidays_dates=_occ_list(_bucket(data, "public_holidays", "public_holiday_dates"), month, year),
        unpaid_leave_dates=_occ_list(_bucket(data, "unpaid_leaves", "unpaid_leave_dates"), month, year),
        absent_dates=_occ_list(_bucket(data, "unauthorized_absences", "absent_dates"), month, year),
    )


def parse_text_extraction(raw: dict[str, Any]) -> TextExtraction:
    content = raw["choices"][0]["message"]["content"] if "choices" in raw else raw
    if isinstance(content, str):
        content = json.loads(content.replace("```json", "").replace("```", "").strip())

    def _as_list(key: str) -> list[str]:
        v = content.get(key) or []
        if isinstance(v, list):
            return [str(x).strip() for x in v if str(x).strip()]
        return [str(v).strip()] if str(v).strip() else []

    return TextExtraction(
        annual_dates=_as_list("annual_dates"),
        work_from_home_dates=_as_list("work_from_home_dates"),
        sick_dates=_as_list("sick_dates"),
        public_holiday_dates=_as_list("public_holiday_dates"),
        unpaid_dates=_as_list("unpaid_dates"),
        absent_dates=_as_list("absent_dates"),
    )


def validate_dates_in_month(model: ExtractedTimesheet) -> list[str]:
    issues: list[str] = []
    if not model.month or not model.year:
        return issues
    last_day = calendar.monthrange(model.year, model.month)[1]
    start = dt.date(model.year, model.month, 1)
    end = dt.date(model.year, model.month, last_day)

    def _check(items: list[LeaveOccurrence], label: str) -> None:
        for occ in items:
            if not (start <= occ.date <= end):
                issues.append(f"{label} date out of range: {occ.date.isoformat()}")

    _check(model.annual_leave_dates, "annual")
    _check(model.work_from_home_dates, "work_from_home")
    _check(model.sick_leave_dates, "sick")
    _check(model.public_holidays_dates, "public_holiday")
    _check(model.unpaid_leave_dates, "unpaid")
    _check(model.absent_dates, "absent")
    return issues
