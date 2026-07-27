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

# One-line cues for pass-1 triage — how to tell this template apart from others.
IDENTIFY_CUES: dict[str, str] = {
    "alpha_adr_attendance": (
        "Green 'ATTENDANCE SHEET' header, EMP NO + NAME + SECTION: ADR, "
        "DATE rows (format varies: '1 June 2026', '01/Aug/2025', etc.), "
        "REGULAR IN/OUT or Attendance Type/Sub Type. May arrive as 4 weekly files."),
    "adnoc_timesheet": (
        "Title 'TIMESHEET', 'Service Provider' name block, Agreement - Alpha Data, "
        "Month/Year row, grid with Normal / Overtime / Total columns."),
    "adnoc_general_attendance": (
        "Title 'General Attendance Report', weekly date blocks, Time In/Out rows, "
        "'Total Daily Duration', Remarks like Day Off / Unauthorized Absence."),
    "gov_employee_daily_report": (
        "Title 'Employee Daily Report' (ST-Supreme), FDF/DMT entity, Emp No, "
        "First In / Last Out / Work Duration / Day column (Rest Day, Holiday, Sick Leave)."),
    "adda_attendance": "ADDA grid with P/WO day codes and Time In/Out.",
    "digital_dubai_report": "Digital Dubai Attendance Report, NORMAL/OFF DAYS/ABSENCE columns.",
    "dewa_moro_smartoffice": "Moro Smart Office Attendance Sheet, PR Number, Notes column.",
    "dewa_professional_hiring": "DEWA Professional Hiring Staff hourly log.",
    "sgrp_smarttime": "SGRP SmartTime export.",
    "damac_excel_timesheet": "DAMAC consultant Excel with Billable hours.",
    "gpssa_daily_report": "GPSSA Attendance Daily Report, Login Time/Status.",
    "endo_arabic_gov": "Endo / Arabic-script government attendance.",
    "leave_certificate": (
        "HR mobile app screenshot (Leave History / My Leaves / ESS) OR "
        "doctor/medical certificate — not a day grid."
    ),
    "approval": "Approval screenshot or signed note — not a full grid.",
    "generic": "No known template matched.",
}

EXTRACT_PROMPTS: dict[str, str] = {
    "alpha_adr_attendance": (
        "FORMAT = Alpha Data ADR 'ATTENDANCE SHEET' (PDF/Excel, one row per calendar day).\n"
        "HEADER: EMP NO → employee_id, NAME → employee_name, SECTION: ADR, "
        "MONTH + YEAR → period, DEPARTMENT/CUSTOMER → context.\n"
        "DATE COLUMN — format varies by client; read what is PRINTED, do not normalise:\n"
        "  '1 June 2026', '2 June 2026'  |  '01/Aug/2025'  |  '1-June-26'  |  "
        "'Monday, 06/01/2026'  |  other DD-Mon-YYYY variants.\n"
        "Count days_covered = number of calendar-day rows you can see (including "
        "weekends labelled Saturday/Sunday/REST DAY). List any day-of-month with "
        "NO row in missing_days.\n"
        "WEEKLY SPLITS: the same employee may send 4 separate files (week 1–4). "
        "Each file is period_type 'week' or 'partial' with only its dates — that is "
        "correct. Do NOT pad missing weeks with invented rows.\n"
        "GRID layouts:\n"
        "  (A) DATE | REGULAR (IN, OUT) | Hours Worked | DAILY TOTAL\n"
        "  (B) DATE | Attendance Type | Sub Type — SUB TYPE wins.\n"
        "Weekend/REST DAY rows are NOT leave. Public Holiday/Public Leave → public_holiday; "
        "Sick/MEDICAL → sick (never annual); Annual → annual; WFH → remote.\n"
        "Clock IN/OUT + hours = WORKED. MANAGER SIGNATURE filled = manager_signature."
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
        "FORMAT = ADNOC 'TIMESHEET' — Service Provider monthly hours log (NOT the "
        "'General Attendance Report' — that is format_id adnoc_general_attendance).\n"
        "HEADER:\n"
        "  'Service Provider' block → employee_name\n"
        "  Agreement - Alpha Data / Position / Department → context\n"
        "  Month/Year (e.g. Jun 2026) → period\n"
        "  ADNOC Classification line → context only\n"
        "GRID: Date | Day | Normal | Overtime | Total — often split into two halves "
        "(days 1-15 and 16-31) on one or two pages.\n"
        "Row with Normal/Total hours and code 'P' = WORKED — not leave.\n"
        "Leave ONLY when a cell explicitly names leave/holiday/absent (not hours alone).\n"
        "Electronic consent / employee signature block at top is NOT manager approval "
        "unless a separate manager sign-off is visible."
        + _JSON_TAIL
    ),
    "adnoc_general_attendance": (
        "FORMAT = ADNOC 'General Attendance Report' (multi-page PDF, weekly layout).\n"
        "HEADER (repeated each page):\n"
        "  'General Attendance Report'\n"
        "  Period From/To (e.g. 01-Jun-2026 … 30-Jun-2026) → month + year\n"
        "  Name line 'First Last - 12345678' → employee_name; trailing number → employee_id\n"
        "  'ADS######## - Name' footer/header → employee_id prefix ADS + name\n"
        "GRID per week block:\n"
        "  Columns: Date | Time In | Time Out | Movement Duration | Work Duration | Remarks\n"
        "  One calendar day may have MULTIPLE In/Out rows (Step Out breaks). Use the "
        "'Total Daily Duration' summary row for that day's worked hours — do NOT "
        "treat Step Out / Movement rows as separate days.\n"
        "REMARKS column (primary leave signal):\n"
        "  'Day Off' → weekend/rest (NOT leave)\n"
        "  'Unauthorized Absence' → absent\n"
        "  '(Public Holyday)' / 'Public Holiday' → public_holiday (typo 'Holyday' is common)\n"
        "  '(Emergency Leave)' → annual (unless sheet legend says otherwise)\n"
        "  'Permission …' / permission notes → NOT leave (ignore for buckets)\n"
        "  Blank remarks + Time In/Out + Total Daily Duration → WORKED\n"
        "Dates print as DD/MM/YYYY with weekday (e.g. 01/06/2026 Monday).\n"
        "Read ALL pages — the month spans 4+ weekly sections. days_covered = unique "
        "calendar dates with a row (worked, Day Off, or absence)."
        + _JSON_TAIL
    ),
    "digital_dubai_report": (
        "FORMAT = Digital Dubai 'Attendance Report' (system export, often multi-page).\n"
        "Identity: 'EMPLOYEE NUMBER' = employee_id, 'EMPLOYEE NAME' = employee_name "
        "(may be Arabic — keep as printed). Period: 'ATTENDANCE PERIOD FROM .. TO ..'.\n"
        "Per-day grid: NORMAL / OFF DAYS / ABSENCE / PERMISSION. "
        "'1' under ABSENCE = absent day; OFF DAYS = weekends (NOT leave). "
        "PERMISSION may show approved annual leave (often with hours) — "
        "treat those days as annual, not as noise. "
        "Only blank ABSENCE/PERMISSION with no leave label = worked. "
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
        "FORMAT = ST-Supreme 'Employee Daily Report' (FDF, DMT, and similar UAE entities).\n"
        "HEADER:\n"
        "  Title 'Employee Daily Report', entity name (e.g. 'FDF Family Development Foundation')\n"
        "  From / To dates (DD/MM/YYYY) → month + year\n"
        "  Employee Name + Emp No → employee_name / employee_id\n"
        "  Company / Entity / Work Location → context\n"
        "GRID (may span 2 pages — read both):\n"
        "  Date | First In | Last Out | Early Out | Delay | Work Duration | Remarks | "
        "Schedule Name | Schedule Type | Lost Time | Over Time | Day\n"
        "DAY column is the primary leave/weekend signal:\n"
        "  'Rest Day' / Saturday / Sunday → weekend (NOT leave)\n"
        "  'Holiday -' / 'Holiday' → public_holiday\n"
        "  'Sick Leave' / 'Sick Leave-OutSocruce' (typo) → sick\n"
        "  'Annual Leave' / 'Emergency Leave' → annual\n"
        "  Weekday with First In + Last Out + Work Duration filled → WORKED (not leave)\n"
        "  Row with only Date + Day label and no In/Out → leave or rest as Day column says\n"
        "Dates: DD/MM/YYYY (e.g. 01/06/2026). Read ALL pages for the full month."
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
        "FORMAT = Leave evidence — medical certificate OR HR mobile-app screenshot "
        "(Leave History, My Leaves, ESS).\n"
        "This is NOT a daily attendance grid. Pass 1 already marked it leave_certificate.\n"
        "HR APP SCREENSHOTS — each card/row shows:\n"
        "  leave type label (e.g. Annual Leave, Sick Leave, Mourning Leave),\n"
        "  date range (e.g. '18 Jun 2026 - 19 Jun 2026'), duration, status (Approved).\n"
        "Expand EVERY calendar day in each Approved range to ISO YYYY-MM-DD.\n"
        "Only include Approved (or clearly taken) records — skip Pending/Rejected.\n"
        "Map types: Annual/Vacation → annual; Sick/Medical → sick; Maternity → maternity; "
        "Unpaid/LWP → unpaid; Public Holiday → public_holiday; Mourning Leave (any degree) → annual; "
        "Official Assignment → remote; Absent → absent.\n"
        "MEDICAL certificates: put certified days in sick (or stated type).\n"
        "month/year = month of the leave dates shown. days_covered = 0; period_type = 'partial'."
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


def identify_cue_for(format_id: str | None) -> str:
    """Short pass-1 identification hint."""
    return IDENTIFY_CUES.get(format_id or "generic") or IDENTIFY_CUES["generic"]


def all_extract_format_ids() -> list[str]:
    return list(EXTRACT_PROMPTS.keys())
