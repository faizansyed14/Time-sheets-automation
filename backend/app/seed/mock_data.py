"""
Single source of truth for ALL mock data.

Both the mock email provider (renders attachments) and the mock extraction
engine (returns "LLM" results) read from here, so the demo stays internally
consistent: what you see in the previewed attachment matches what the pipeline
extracts.

Replace this entire module with nothing — it is only imported by the *mock*
provider/engine. The Graph provider and Vision engine ignore it.
"""
from __future__ import annotations

from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Employee Matcher list -> seeds all_employee_data
# ---------------------------------------------------------------------------
EMPLOYEE_MATCHER = [
    # employee_id, name,            dco,       account_manager, email, location
    ("EMP-1001", "Mohammed Ali", "DCO-552", "Sarah Khan", "mohammed.ali@company.com", "DXB"),
    ("EMP-1002", "Priya Sharma", "DCO-553", "Sarah Khan", "priya.sharma@company.com", "DXB"),
    ("EMP-1003", "John Mathew", "DCO-554", "David Lee", "john.mathew@company.com", "DXB"),
    ("EMP-1004", "Aisha Rahman", "DCO-555", "David Lee", "aisha.rahman@company.com", "AUH"),
    ("EMP-1005", "Carlos Mendes", "DCO-556", "Sarah Khan", "carlos.mendes@company.com", "DXB"),
    ("EMP-1006", "Fatima Noor", "DCO-557", "David Lee", "fatima.noor@company.com", "AUH"),
    # EMP-2001 exists in BOTH teams (different people!) — exercises the
    # employee_id + name matching.
    ("EMP-2001", "Rahul Verma", "DCO-601", "Sarah Khan", "rahul.verma@company.com", "DXB"),
    ("EMP-2001", "Ahmed Hassan", None, "Omar Farouk", "ahmed.hassan@company.com", "AUH"),
]


# ---------------------------------------------------------------------------
# A "case" = one timesheet inside one email.
# Each email (message) holds one or more cases (attachments).
# `issues` drive the validation summary (yellow rows); empty -> verified (green).
# ---------------------------------------------------------------------------
def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


# Each message: id, sender, subject, received, body, and a list of cases.
# A case: slot (attachment index), doc kind ("pdf"/"docx"), employee identity
#         as it appears ON THE SHEET, month/year, leave buckets, approval png?,
#         approval_detected (what the LLM would read from the screenshot),
#         and any extraction issues.
MESSAGES = [
    {
        "message_id": "MSG-0001",
        "sender_name": "Mohammed Ali",
        "sender_email": "mohammed.ali@company.com",
        "subject": "January 2026 Timesheet - Mohammed Ali",
        "received_at": _dt("2026-02-02T09:14:00"),
        "body_text": "Hi HR,\n\nPlease find attached my timesheet for January 2026 along "
                     "with the manager approval screenshot.\n\nThanks,\nMohammed",
        # Outlook-style HTML body with an inline signature logo (cid:). The
        # backend resolves cid: → data URI so it renders like Outlook.
        "body_html": (
            "<div style=\"font-family:Calibri,Arial,sans-serif;font-size:14px;color:#1f2937\">"
            "<p>Hi HR,</p>"
            "<p>Please find attached my timesheet for <b>January 2026</b> along with the "
            "manager approval screenshot.</p>"
            "<p>Thanks,<br>Mohammed Ali</p>"
            "<table style=\"margin-top:16px;border-top:1px solid #e5e7eb;padding-top:8px\"><tr>"
            "<td><img src=\"cid:alphalogo\" alt=\"Alpha Data\" width=\"180\"></td>"
            "</tr></table>"
            "<p style=\"color:#16a34a;font-style:italic;font-size:12px\">"
            "Please consider your environmental responsibility before printing this e-mail.</p>"
            "</div>"
        ),
        "inline_images": [
            {"slot": "logo", "cid": "alphalogo", "filename": "alpha_data_footer_gradient1.png"},
        ],
        "cases": [
            {
                "slot": "ts",
                "doc": "pdf",
                "emp_id": "EMP-1001",
                "emp_name": "Mohammed Ali",
                "month": 1, "year": 2026,
                "annual": ["2026-01-06", "2026-01-07", "2026-01-08"],
                "sick": ["2026-01-20"],
                "public_holiday": ["2026-01-01"],
                "issues": [],
            },
        ],
        "approval": {"slot": "ap", "detected": True, "detail": "Approved by Sarah Khan on 01-Feb-2026"},
    },
    {
        "message_id": "MSG-0002",
        "sender_name": "Priya Sharma",
        "sender_email": "priya.sharma@company.com",
        "subject": "Timesheet Jan - Priya",
        "received_at": _dt("2026-02-02T10:02:00"),
        "body_text": "Hello,\n\nAttaching my January timesheet (Word doc) and the approval mail screenshot.\n\nPriya",
        "cases": [
            {
                "slot": "ts",
                "doc": "docx",
                "emp_id": "EMP-1002",
                "emp_name": "Priya Sharma",
                "month": 1, "year": 2026,
                "header_month": 2, "header_year": 2026,   # top of sheet says Feb, rows are Jan
                "annual": ["2026-01-12", "2026-01-13", "2026-01-13"],  # duplicate 13th
                "sick": [],
                "public_holiday": ["2026-01-01"],
                "issues": ["Duplicate date 2026-01-13 found in Annual leave."],
            },
        ],
        "approval": {"slot": "ap", "detected": False, "detail": "Screenshot unclear — approval status not legible"},
    },
    {
        "message_id": "MSG-0003",
        "sender_name": "John Mathew",
        "sender_email": "john.mathew@company.com",
        "subject": "Feb 2026 timesheet",
        "received_at": _dt("2026-03-01T08:40:00"),
        "body_text": "Hi team, February timesheet attached. No leaves except one remote-work day.\n\nJohn",
        "cases": [
            {
                "slot": "ts",
                "doc": "pdf",
                "emp_id": "EMP-1003",
                "emp_name": "John Mathew",
                "month": 2, "year": 2026,
                "annual": [],
                "remote": ["2026-02-10"],
                "sick": [],
                "public_holiday": [],
                "issues": [],
            },
        ],
        "approval": None,  # no approval screenshot in this email
    },
    {
        "message_id": "MSG-0004",
        "sender_name": "Mohd Ali",  # name variant, no employee id on the sheet
        "sender_email": "mohammed.ali@company.com",
        "subject": "Feb timesheet",
        "received_at": _dt("2026-03-01T11:25:00"),
        "body_text": "Feb timesheet attached.",
        "cases": [
            {
                "slot": "ts",
                "doc": "pdf",
                "emp_id": "",                 # missing id -> forces fuzzy name match
                "emp_name": "Mohd Ali",       # variant of "Mohammed Ali"
                "month": 2, "year": 2026,
                "annual": ["2026-02-17"],
                "absent": ["2026-02-24"],
                "public_holiday": [],
                "issues": [],
            },
        ],
        "approval": {"slot": "ap", "detected": True, "detail": "Approved by Sarah Khan"},
    },
    {
        "message_id": "MSG-0005",
        "sender_name": "Aisha Rahman",
        "sender_email": "aisha.rahman@company.com",
        "subject": "January Timesheet - Aisha",
        "received_at": _dt("2026-02-03T14:05:00"),
        "body_text": "Hi, attaching January timesheet.",
        "cases": [
            {
                "slot": "ts",
                "doc": "pdf",
                "emp_id": "EMP-1004",
                "emp_name": "Aisha Rahman",
                "month": 1, "year": 2026,
                "annual": ["2026-01-15", "2026-02-02"],  # 02-Feb is outside January
                "sick": ["2026-01-28"],
                "public_holiday": ["2026-01-01"],
                "issues": ["Date 2026-02-02 is outside the timesheet month (January 2026)."],
            },
        ],
        "approval": {"slot": "ap", "detected": True, "detail": "Approved by David Lee on 03-Feb-2026"},
    },
    {
        "message_id": "MSG-0006",
        "sender_name": "Sarah Khan",  # manager forwarding TWO people in one email
        "sender_email": "sarah.khan@company.com",
        "subject": "Feb timesheets - Carlos & Fatima (team)",
        "received_at": _dt("2026-03-02T09:00:00"),
        "body_text": "HR,\n\nForwarding February timesheets for two of my team members. "
                     "Both approved.\n\nSarah",
        "cases": [
            {
                "slot": "ts1",
                "doc": "pdf",
                "emp_id": "EMP-1005",
                "emp_name": "Carlos Mendes",
                "month": 2, "year": 2026,
                "annual": ["2026-02-18", "2026-02-19"],
                "public_holiday": [],
                "issues": [],
            },
            {
                "slot": "ts2",
                "doc": "pdf",
                "emp_id": "EMP-1006",
                "emp_name": "Fatima Noor",
                "month": 2, "year": 2026,
                "sick": ["2026-02-05", "2026-02-06"],
                "public_holiday": [],
                "issues": [],
            },
        ],
        "approval": {"slot": "ap", "detected": True, "detail": "Approved by Sarah Khan (team lead)"},
    },
    {
        # WEEKLY timesheets: one employee, one month, TWO files. Both must be
        # accepted and MERGED into a single March record (not deduped away).
        "message_id": "MSG-0007",
        "sender_name": "Rahul Verma",
        "sender_email": "rahul.verma@company.com",
        "subject": "March 2026 weekly timesheets (Week 1-2 and Week 3-4) - Rahul",
        "received_at": _dt("2026-04-01T09:30:00"),
        "body_text": "Hi HR,\n\nOur client requires weekly timesheets, so March comes in two "
                     "files: weeks 1-2 and weeks 3-4. Both attached with approval.\n\nRahul",
        "cases": [
            {
                "slot": "ts1",
                "doc": "pdf",
                "emp_id": "EMP-2001",
                "emp_name": "Rahul Verma",
                "month": 3, "year": 2026,
                "period_label": "Week 1-2 (01-14 Mar)",
                "annual": ["2026-03-03", "2026-03-04"],
                "public_holiday": [],
                "issues": [],
            },
            {
                "slot": "ts2",
                "doc": "pdf",
                "emp_id": "EMP-2001",
                "emp_name": "Rahul Verma",
                "month": 3, "year": 2026,
                "period_label": "Week 3-4 (15-31 Mar)",
                "sick": ["2026-03-19"],
                "remote": ["2026-03-25"],
                "public_holiday": [],
                "issues": [],
            },
        ],
        "approval": {"slot": "ap", "detected": True, "detail": "Approved by Sarah Khan on 31-Mar-2026"},
    },
    {
        # SAME employee_id as Rahul (EMP-2001) but the AUH person. The name
        # must route this to Ahmed Hassan, NOT Rahul Verma.
        "message_id": "MSG-0008",
        "sender_name": "Ahmed Hassan",
        "sender_email": "ahmed.hassan@company.com",
        "subject": "March 2026 Timesheet - Ahmed Hassan (AUH)",
        "received_at": _dt("2026-04-01T10:05:00"),
        "body_text": "Dear HR,\n\nPlease find my March timesheet attached.\n\nAhmed (Abu Dhabi office)",
        "cases": [
            {
                "slot": "ts",
                "doc": "pdf",
                "emp_id": "EMP-2001",
                "emp_name": "Ahmed Hassan",
                "month": 3, "year": 2026,
                "annual": ["2026-03-10", "2026-03-11"],
                "public_holiday": [],
                "issues": [],
            },
        ],
        "approval": {"slot": "ap", "detected": True, "detail": "Approved by Omar Farouk on 31-Mar-2026"},
    },
    {
        # Pipeline-failure showcase #1: an employee who is NOT in the matcher
        # list, and a sheet with NO name / NO id printed on it.
        "message_id": "MSG-0009",
        "sender_name": "Zara Iqbal",
        "sender_email": "zara.iqbal@contractor.com",
        "subject": "Feb timesheets - new joiners",
        "received_at": _dt("2026-03-02T15:45:00"),
        "body_text": "Hello,\n\nTimesheets for February attached (mine and one from a colleague "
                     "whose sheet template is missing the name field).\n\nZara",
        "cases": [
            {
                "slot": "ts1",
                "doc": "pdf",
                "emp_id": "",
                "emp_name": "Zara Iqbal",       # not in all_employee_data
                "month": 2, "year": 2026,
                "annual": ["2026-02-11"],
                "public_holiday": [],
                "issues": [],
            },
            {
                "slot": "ts2",
                "doc": "pdf",
                "emp_id": "",
                "emp_name": "",                  # nothing to identify the person
                "month": 2, "year": 2026,
                "sick": ["2026-02-12"],
                "public_holiday": [],
                "issues": [],
            },
        ],
        "approval": None,
    },
    {
        # Pipeline-failure showcase #2: a password-protected PDF and a sheet
        # whose shared ID can't be disambiguated by the printed name.
        "message_id": "MSG-0010",
        "sender_name": "Omar Farouk",
        "sender_email": "omar.farouk@company.com",
        "subject": "March timesheets - protected file + unclear name",
        "received_at": _dt("2026-04-02T08:20:00"),
        "body_text": "HR,\n\nForwarding two March timesheets. One came back from the client "
                     "password-protected; the other prints only initials.\n\nOmar",
        "cases": [
            {
                "slot": "ts1",
                "doc": "pdf",
                "protected": True,               # rendered as an encrypted PDF
                "emp_id": "EMP-1006",
                "emp_name": "Fatima Noor",
                "month": 3, "year": 2026,
                "annual": ["2026-03-09"],
                "public_holiday": [],
                "issues": [],
            },
            {
                "slot": "ts2",
                "doc": "pdf",
                "emp_id": "EMP-2001",            # shared AUH/DXB id...
                "emp_name": "R. K.",             # ...and a name that fits nobody
                "month": 3, "year": 2026,
                "remote": ["2026-03-12"],
                "public_holiday": [],
                "issues": [],
            },
        ],
        "approval": {"slot": "ap", "detected": True, "detail": "Approved by Omar Farouk"},
    },
]


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------
def message_by_id(message_id: str) -> dict | None:
    return next((m for m in MESSAGES if m["message_id"] == message_id), None)


def attachment_id(message_id: str, slot: str) -> str:
    return f"{message_id}::{slot}"


def case_for_attachment(att_id: str) -> dict | None:
    """Given an attachment id, return its timesheet case (or None if it's the approval png)."""
    if "::" not in att_id:
        return None
    msg_id, slot = att_id.split("::", 1)
    msg = message_by_id(msg_id)
    if not msg:
        return None
    for c in msg["cases"]:
        if c["slot"] == slot:
            return c
    return None


def approval_for_message(message_id: str) -> dict | None:
    msg = message_by_id(message_id)
    return msg.get("approval") if msg else None
