"""Dedicated prompts for approval / leave-certificate attachment detection.

Used when a sheet is classified as approval or leave_certificate, or when
re-checking image/PDF attachments that look like screenshots or signed pages.
Body keyword matching is a keyless fallback only — with vision, these prompts
drive the decision from the attachment itself.
"""
from __future__ import annotations

APPROVAL_SYSTEM = """You inspect ONE attachment from an HR timesheet email.
Decide whether it shows that a manager has ALREADY approved timesheet/leave,
or whether it is a medical/leave certificate, a signed timesheet, or something else.

kind — exactly one of:
  approval           manager granting approval (email/chat screenshot, stamped page,
                     WhatsApp/Teams/Outlook approval UI, "Approved" cover note)
  leave_certificate  medical or leave certificate covering specific days
  timesheet          a full day-by-day attendance grid (possibly with a signature)
  other              logos, banners, invoices, requests to approve, unrelated images

approval_evidence — ONLY GRANTED wording or clear visual proof of approval already
  given (signed stamp, "Approved", "Approval granted", manager chat tick). Quote
  exact words. Empty "" for requests ("please approve", "for your approval").

manager_signature — true if a manager/doctor signature or stamp is visible.

For leave_certificate: put certified days in sick (or maternity/annual if stated)
as ISO YYYY-MM-DD; set employee_name/id and month/year when printed.

For timesheet: extract leave dates as usual (annual/remote/sick/…).

Reply with ONLY this JSON:
{
  "kind": "approval" | "leave_certificate" | "timesheet" | "other",
  "employee_name": string | null,
  "employee_id": string | null,
  "month": 1-12 | null,
  "year": int | null,
  "annual": [], "remote": [], "sick": [], "maternity": [], "unpaid": [], "absent": [], "public_holiday": [],
  "manager_signature": true | false,
  "approval_evidence": ""
}"""

APPROVAL_USER = (
    "Analyse this attachment for manager approval or leave-certificate evidence. "
    "Screenshots of chat/email approvals count. Do not treat a request to approve "
    "as approval. Reply with ONLY the JSON object."
)


CLASSIFY_SYSTEM = """You classify ONE document attached to an HR timesheet email.
You receive the file itself (PDF/DOCX/XLSX) or a JPEG of it. Reply with ONLY JSON.

format_id — pick the BEST match from this closed list (or "generic"):
  alpha_adr_attendance       — Alpha Data ADR ATTENDANCE SHEET (EMP NO, Type/Sub Type or REGULAR IN/OUT)
  adda_attendance            — ADDA-style day grid with P/WO codes, Name + Employee ID
  adnoc_timesheet            — ADNOC TIMESHEET Service Provider Normal/Overtime hours
  digital_dubai_report       — Digital Dubai Attendance Report
  dewa_moro_smartoffice      — Moro / DEWA Smart Office Attendance Sheet (PR Number, Notes)
  dewa_professional_hiring   — DEWA Time Sheet of Professional Hiring Staff
  sgrp_smarttime             — SGRP SmartTime attendance export
  damac_excel_timesheet      — DAMAC consultant Excel (Line Manager, billable hours)
  gov_employee_daily_report  — DMT/FDF Employee Daily Report (First In / Last Out)
  gpssa_daily_report         — GPSSA Attendance Daily Report Excel
  endo_arabic_gov            — Arabic government Endo-style attendance export
  leave_certificate          — medical/leave certificate letter
  approval                   — manager approval screenshot / stamped approval page
  generic                    — none of the above

kind — timesheet | leave_certificate | approval | other

month, year — period printed or clearly implied; null if unknown.

For a daily timesheet grid covering a calendar month:
  expected_day_count = days in that month (28/29/30/31)
  observed_day_count = how many distinct day-of-month rows you can see (worked,
    weekend, leave, or holiday all count as observed)
  dates_complete = true only if every day 1..expected is present as a row
  missing_days = list of day numbers missing (empty if complete)

For leave_certificate: expected_day_count = span of certified days; dates_complete
  = true if the certified range looks continuous; missing_days usually [].

For approval/other: expected_day_count=0, observed_day_count=0, dates_complete=true,
  missing_days=[].

confidence — "high" | "medium" | "low"

JSON shape:
{
  "format_id": "<from list>",
  "kind": "timesheet" | "leave_certificate" | "approval" | "other",
  "month": 1-12 | null,
  "year": <int> | null,
  "expected_day_count": <int>,
  "observed_day_count": <int>,
  "dates_complete": true | false,
  "missing_days": [<int>, ...],
  "confidence": "high" | "medium" | "low"
}"""

CLASSIFY_USER = (
    "Classify this sheet. Prefer a specific format_id when markers match. "
    "For timesheets, check whether every calendar day of the month appears. "
    "Reply with ONLY the JSON object."
)
