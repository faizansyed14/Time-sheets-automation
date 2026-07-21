"""Single extraction prompt for Extract Email (one sheet per call)."""
from __future__ import annotations

from app.core.pii import scrub_email_for_llm, scrub_text
from app.models.email_message import EmailMessage
from app.services.extract_email.format_prompts import extract_prompt_for
from app.services.extract_email.types import SheetUnit

# One extraction prompt body. Format-specific rules are appended per call by
# extract_prompt() — never duplicated in a separate system/user pair.
EXTRACT_PROMPT = """You read ONE document from an HR timesheet email.
You receive the file itself (PDF/DOCX/XLSX) or one JPEG, plus exact extracted text when available.

Report for THIS sheet only:

kind — exactly one of:
  timesheet          day-by-day attendance / hours / leave grid
  leave_certificate  medical or leave certificate covering specific days
  approval           manager already approving timesheet/leave (screenshot or note)
  other              cover notes, logos, banners, invoices, tickets, receipts,
                     purchase orders, or any financial table (S.# / Amount / VAT /
                     Invoice# / PNR) — NEVER classify those as timesheet

employee_name, employee_id — EXACTLY as printed on this sheet; null if absent.
  Never copy identity from another sheet or from an email address.

month, year — period printed on the sheet (or clearly implied by its dates); null if absent.

Leave dates as ISO YYYY-MM-DD, each date in exactly ONE list:
  annual | remote | sick | maternity | unpaid | absent | public_holiday
  Normal worked days and weekends are NOT leave. Empty grid → empty lists.
  MERGED / SPANNING MARKS: one label across several date rows applies to EVERY
  date it covers. COLOR-CODED: use the legend; map each colour to leave type.
  leave_certificate days → usually sick (or the type stated).

manager_signature — true only if THIS sheet shows a manager/supervisor signature,
  stamp, or signed approval block.

approval_evidence — ONLY when a manager has ALREADY granted approval (signed/
  stamped block, or wording like "Approved", "Approval granted"). Quote exact
  words; otherwise "". Requests ("please approve", "for your approval",
  "pending approval") and rejections are NOT approval → "".

Sheet names are labels only — "body_timesheet.png" is a placeholder; judge kind
from content, never from the name.

Special case — sheet named "(email body)":
  timesheet ONLY if a day-by-day attendance/leave grid is pasted as TEXT in the
  thread. If the grid is a separate image sheet, the body is "other" (or approval
  wording only). Mentioning "timesheet", an employee ID, or a Subject line is
  NOT a grid. No month/year/employee/leave on a body that has no per-day rows.

Never invent values — unsure → null / empty.
Reply with ONLY this JSON object (no sheets array):
{
  "kind": "timesheet" | "leave_certificate" | "approval" | "other",
  "employee_name": "<exactly as printed>" | null,
  "employee_id": "<exactly as printed>" | null,
  "month": 1-12 | null,
  "year": <int> | null,
  "annual": ["YYYY-MM-DD", ...],
  "remote": [], "sick": [], "maternity": [], "unpaid": [], "absent": [], "public_holiday": [],
  "manager_signature": true | false,
  "approval_evidence": ""
}"""


def system_prompt() -> str:
    """Backward-compatible alias — extraction uses extract_prompt() only."""
    return EXTRACT_PROMPT


def extract_prompt(
    email: EmailMessage,
    unit: SheetUnit,
    *,
    native: bool = False,
) -> str:
    """The single user prompt for one sheet (format rules injected once)."""
    subject, _ = scrub_email_for_llm(email.subject, "")
    fid = getattr(unit, "format_id", None) or "generic"
    clf = getattr(unit, "classify", None)
    kind_hint = getattr(clf, "kind", None) if clf else None
    if kind_hint == "approval" or fid == "approval":
        format_body = extract_prompt_for("approval")
    elif kind_hint == "leave_certificate" or fid == "leave_certificate":
        format_body = extract_prompt_for("leave_certificate")
    else:
        format_body = extract_prompt_for(fid)

    lines = [
        EXTRACT_PROMPT,
        "",
        f"EMAIL SUBJECT: {subject}",
        f'SHEET: "{unit.name}" ({unit.ftype})',
        ("Attached as a native file — read the file."
         if native else
         "Attached as one image (or text only if no image)."),
        "",
        "FORMAT RULES FOR THIS SHEET:",
        format_body,
    ]
    if unit.text:
        sheet_text = scrub_text(unit.text)[:8000]
        lines.append(
            f'\n--- EXACT TEXT OF "{unit.name}" '
            "(trust over the image for names, IDs and dates) ---\n"
            + sheet_text
        )
    lines.append("\nAnalyse this sheet. Reply with ONLY the JSON object.")
    return "\n".join(lines)


def system_prompt_for(format_id: str | None, kind: str | None = None) -> str:
    """Base extract rules + one format body (used by unit tests / previews)."""
    fid = format_id or "generic"
    if kind == "approval" or fid == "approval":
        body = extract_prompt_for("approval")
    elif kind == "leave_certificate" or fid == "leave_certificate":
        body = extract_prompt_for("leave_certificate")
    else:
        body = extract_prompt_for(fid)
    return EXTRACT_PROMPT + "\n\nFORMAT RULES:\n" + body
