"""Per-format extraction prompt bodies (requirement: dedicated prompt per template).

Keys match FormatSpec.id in formats.py. GENERIC is the fallback. These strings
are appended to the shared system prompt when extracting a sheet of that format
— telling the model exactly where identity, period, leave markers and approvals
live in THAT template.
"""
from __future__ import annotations

# Shared JSON contract reminder appended after every format-specific body.
_JSON_TAIL = (
    "\nReply with ONLY the requested JSON for this sheet. Never invent values — "
    "when unsure use null / empty lists. Normal worked days and weekends are NOT leave."
)

EXTRACT_PROMPTS: dict[str, str] = {
    "alpha_adr_attendance": (
        "FORMAT = Alpha Data ADR 'ATTENDANCE SHEET' (one row per calendar day).\n"
        "Identity: 'EMP NO' = employee_id, 'NAME' = employee_name, "
        "'MONTH'/'YEAR' = period. Customer name may appear as CUSTOMER.\n"
        "Two layouts — handle whichever is present:\n"
        "  (a) columns 'Attendance Type' + 'Sub Type' — SUB TYPE wins when both set;\n"
        "  (b) columns 'REGULAR (IN/OUT)' + 'Hours Worked' + 'DAILY TOTAL' — leave word "
        "is written in the row (e.g. 'Sick Leave', 'Public Holiday', 'REST DAY').\n"
        "Map labels EXACTLY: Public Holiday -> public_holiday; Sick Leave/Sick -> sick; "
        "Annual Leave/Annual -> annual; WFH/Remote/Official WFH -> remote; Unpaid/LOP -> unpaid; "
        "Absent -> absent; Maternity -> maternity. Clock IN/OUT + hours = WORKED (not leave). "
        "Saturday/Sunday/Weekend/REST DAY/Weekly Off = weekends (not leave).\n"
        "Read EVERY day 1..end-of-month. Blank 'MANAGER SIGNATURE' = manager_signature=false; "
        "filled name/stamp = true and quote any GRANTED approval wording."
        + _JSON_TAIL
    ),
    "adda_attendance": (
        "FORMAT = ADDA / ADR-style attendance grid with day codes.\n"
        "Identity: 'Name' / 'Employee ID' (e.g. E2206236), 'Month' like Nov-23 or Nov-2023.\n"
        "Each calendar day has a code: P = present/worked; WO = week off (weekend, NOT leave); "
        "leave codes may appear as SL/AL/A/PH or spelled leave types in the cell.\n"
        "Map: SL/Sick -> sick; AL/Annual -> annual; A/Absent -> absent; PH/Holiday -> public_holiday; "
        "WFH/Remote -> remote; Unpaid/LOP -> unpaid; Maternity -> maternity.\n"
        "Include Time In/Out only as evidence of worked days — do not put worked days in leave buckets. "
        "Read every day of the month."
        + _JSON_TAIL
    ),
    "adnoc_timesheet": (
        "FORMAT = ADNOC TIMESHEET (Service Provider hours log).\n"
        "Identity: 'Service Provider' = employee_name; Agreement / Position / Department as context; "
        "'Month/Year' = period (e.g. Jun 2026).\n"
        "Grid columns: Date, Day, Normal, Overtime, Total (often split across two halves of the month).\n"
        "A row with Normal/Total hours and code 'P' is WORKED — not leave. "
        "Leave only when the cell explicitly names leave/holiday/absent. "
        "Electronic signature / consent block at top is NOT manager_signature unless a manager "
        "approval/sign-off block is clearly present."
        + _JSON_TAIL
    ),
    "digital_dubai_report": (
        "FORMAT = Digital Dubai 'Attendance Report' (system export, often multi-page).\n"
        "Identity: 'EMPLOYEE NUMBER' = employee_id, 'EMPLOYEE NAME' = employee_name "
        "(may be Arabic — keep as printed). Period: 'ATTENDANCE PERIOD FROM .. TO ..'.\n"
        "Per-day grid: NORMAL / OFF DAYS / ABSENCE / PERMISSION. "
        "'1' under ABSENCE = absent day; OFF DAYS = weekends (NOT leave); "
        "PERMISSION is NOT leave. Only ABSENCE days go in 'absent'. "
        "Ignore summary/overview pages — read 'EMPLOYEE ATTENDANCE DETAILS' rows."
        + _JSON_TAIL
    ),
    "dewa_moro_smartoffice": (
        "FORMAT = DEWA / Moro Smart Office 'Attendance Sheet'.\n"
        "Identity: 'Name' = employee_name, 'PR Number' = employee_id, 'Month / Year' = period, "
        "'Manager' = manager name (context only).\n"
        "Leave is in the NOTES column: Annual Leave -> annual, Sick Leave -> sick, "
        "'EID AL ADHA HOLIDAY' / any '... HOLIDAY' -> public_holiday, Absent -> absent. "
        "Sat/Sun notes = weekends (not leave). Clock In/Out with hours = WORKED.\n"
        "'Approval Status' APPROVED with manager email + 'Approved on' timestamp = "
        "GRANTED approval (approval_evidence) and manager_signature=true."
        + _JSON_TAIL
    ),
    "dewa_professional_hiring": (
        "FORMAT = DEWA 'Time Sheet of Professional Hiring Staff' (hourly log).\n"
        "Identity: 'Employee Name', 'Employee ID No.'. Rows are hourly office-work "
        "(start/end/hours) = WORKED, NOT leave. Leave buckets stay EMPTY unless a row "
        "explicitly names a leave type. 'Approved By' with signature/date = manager_signature."
        + _JSON_TAIL
    ),
    "sgrp_smarttime": (
        "FORMAT = SGRP SmartTime attendance export.\n"
        "Read per-day rows. Clock in/out or hours = WORKED. Only rows flagged leave/absent/"
        "holiday go in leave buckets. Weekends/rest days are not leave."
        + _JSON_TAIL
    ),
    "damac_excel_timesheet": (
        "FORMAT = DAMAC Properties consultant timesheet (Excel).\n"
        "Identity: 'Resource/Consultant Name' = employee_name; 'Line Manager' = manager context; "
        "PO Number / Department as context. Period from Date column (dd/mm/yy).\n"
        "Columns: Date, Task Description, Total Hours (Billable), Public Holiday, Leaves.\n"
        "A date with billable hours = WORKED. Mark public_holiday when Public Holiday column set; "
        "put leave dates in annual (or the named leave type if printed). "
        "Line Manager approval field filled/signed = manager_signature=true."
        + _JSON_TAIL
    ),
    "gov_employee_daily_report": (
        "FORMAT = Government 'Employee Daily Report' (DMT, FDF, similar entities).\n"
        "Header: From/To period dates; Company/Entity; Employee Name + Emp No.\n"
        "Columns: Date, First In, Last Out, Early Out, Delay, Work Duration, Remarks, "
        "Schedule Name/Type, Lost Time, Over Time, Day.\n"
        "Rows with In/Out and Work Duration = WORKED. Leave only from Remarks (Sick/Annual/"
        "Absent/Holiday etc.). Weekends may appear as schedule type — not leave unless Remarks say leave."
        + _JSON_TAIL
    ),
    "gpssa_daily_report": (
        "FORMAT = GPSSA Attendance Daily Report (Excel, often colour-coded).\n"
        "Identity: 'Employee:' name; period 'from (01-MON-YYYY) to (DD-MON-YYYY)' or Date From/To.\n"
        "Columns include Date, Login Time, Login Status / attendance status, etc. "
        "Colour fills may encode leave — use any legend or status text. "
        "Present/login = WORKED; map Sick/Annual/Absent/Holiday status text to leave buckets. "
        "Cover every day in the stated period."
        + _JSON_TAIL
    ),
    "endo_arabic_gov": (
        "FORMAT = Endo / Arabic government attendance system export (often image-heavy PDF).\n"
        "Read VISUALLY: employee name/id may be Arabic or bilingual — keep as printed. "
        "Find the month period and the daily attendance grid. Map absence/leave/holiday marks "
        "using any legend. Worked days with clock times are NOT leave. "
        "Prefer image over garbled digital text if text looks corrupted."
        + _JSON_TAIL
    ),
    "leave_certificate": (
        "FORMAT = Medical / leave certificate or letter (not a day grid).\n"
        "kind MUST be leave_certificate. Extract employee_name/id if printed. "
        "Put certified days in the matching leave bucket (usually sick; maternity/annual if stated). "
        "month/year = period covered by the certificate. manager_signature if doctor/stamp present. "
        "Do NOT invent a full-month timesheet grid."
        + _JSON_TAIL
    ),
    "approval": (
        "FORMAT = Manager approval evidence (email/chat screenshot, stamped approval page, "
        "signed cover note).\n"
        "kind MUST be approval (or timesheet if the image is a full signed timesheet — then "
        "also extract leave dates). Record approval_evidence ONLY for GRANTED wording "
        "('Approved', 'Approval granted', signed-off). Requests ('please approve') are NOT approval. "
        "manager_signature=true when a visible signature/stamp/chat approval from a manager is present."
        + _JSON_TAIL
    ),
    "generic": (
        "FORMAT = Unknown / generic document.\n"
        "Decide kind from content: timesheet (day grid), leave_certificate, approval, or other. "
        "Extract identity and leave dates only when clearly printed. "
        "Invoices/tickets/receipts = other (never timesheet)."
        + _JSON_TAIL
    ),
}


def extract_prompt_for(format_id: str | None) -> str:
    """Full format-specific extraction guidance (never empty for known ids)."""
    return EXTRACT_PROMPTS.get(format_id or "generic") or EXTRACT_PROMPTS["generic"]


def all_extract_format_ids() -> list[str]:
    return list(EXTRACT_PROMPTS.keys())
