"""The two roster-specific vision prompts.

Deliberately SEPARATE from services/extract_email/thread_prompt.py — those
prompts are tuned for "one document, one employee" and are reused byte-for-
byte by Inbox/Upload/Portal. Nothing here touches them.

Why two prompts instead of one:

    A single "read this whole roster" call has to emit headcount x 31 cells
    in one JSON reply. For 8 people that is fine; for 100 it is thousands of
    fields, and long structured replies degrade in a specific, dangerous way
    — the model keeps the shape valid but starts SKIPPING rows. A dropped
    employee is invisible in the output, which is precisely the failure the
    user must never hit.

    Splitting the work removes that failure mode by construction:

      CENSUS  one cheap call per page that reads ONLY the index columns —
              who is on this roster. Small output even at 100+ people, and
              it establishes the authoritative headcount everything else is
              reconciled against.

      GRID    one call per small BATCH of employees that reads only those
              rows' day cells. Each call's output stays small, so it stays
              accurate, and any row a batch misses is detectable (the census
              says who should be there) and retried individually.
"""
from __future__ import annotations

CENSUS_SYSTEM = """You are reading a MULTI-EMPLOYEE monthly attendance roster: one printed
table where each ROW is a different person and the day columns to the right are that
person's calendar days.

Your ONLY job in this pass is to list WHO is on the roster and copy the summary
numbers printed next to each name. Do NOT read the day-by-day grid in this pass.

The single most important requirement: list EVERY employee row, in printed order,
with none skipped and none invented. If the table continues past the bottom of the
image, list everyone you can see. Missing a person is a serious error; a person you
list who is not really there is equally serious."""

CENSUS_USER_RULES = """WHAT TO READ

Document header (once, from the title/top area):
  - month and year of the period (e.g. "Approved Monthly Timesheet Jul 2026" -> 7, 2026)
  - "Calendar Days: N" if printed
  - the agency/vendor name if printed

Per employee row, copy EXACTLY what is printed:
  - sr_no          the Sr No / S.No / serial number of the row, as an integer
  - name           the Resource Name / Employee Name, verbatim
  - title          the Title / Designation column, verbatim, if present
  - location       the Loc. / Location column, if present
  - employee_id    ONLY if the roster actually has an employee-ID column; else null
  - confirmation   the "Emp. Timesheet Confirmation" / status column, if present
  - leave_days     the number printed in the "Leave Days" column (number, not text)
  - billing_days   the number printed in the "Billing Days" column

RULES
- Copy names character-for-character. Do not correct spelling, expand initials,
  reorder first/last, or tidy capitalisation.
- A name that wraps onto two printed lines is still ONE employee.
- If a column does not exist on this roster, use null. Never invent a value.
- leave_days / billing_days must be the printed numbers. If a cell is blank, use null
  rather than guessing 0.
- Do not merge two people who share a name — they are separate rows with separate
  sr_no values."""

CENSUS_OUTPUT = """Return EXACTLY this JSON and nothing else (no markdown fence):
{
  "month": <1-12 or null>,
  "year": <4-digit year or null>,
  "calendar_days": <integer or null>,
  "agency": "<string or null>",
  "employees": [
    {
      "sr_no": <integer>,
      "name": "<verbatim>",
      "title": "<string or null>",
      "location": "<string or null>",
      "employee_id": "<string or null>",
      "confirmation": "<string or null>",
      "leave_days": <number or null>,
      "billing_days": <number or null>
    }
  ],
  "rows_visible": <integer: how many employee rows you could see in total>
}"""


GRID_SYSTEM = """You are transcribing the DAY-BY-DAY cells of a multi-employee monthly
attendance roster. Each row is one person; each column to the right of the name block is
one calendar day of the month, numbered 1..N across the top.

You will be told exactly WHICH rows to transcribe. Transcribe those rows and no others.

Copy each day cell's code EXACTLY as printed. Do not interpret it, do not translate it
into a leave type, do not normalise it, and never leave a day out. Accuracy of the cell
codes is the entire purpose of this pass."""

GRID_USER_RULES = """HOW TO READ A ROW
- Find the row by its Sr No and name. Work left to right across every day column.
- Emit one entry per calendar day from 1 to the stated number of days, with no gaps.
- Copy the code verbatim: "P", "WK", "L", "AB", "HD", "PH", or whatever the sheet uses.
- A genuinely EMPTY cell is "" (empty string). Do NOT substitute "P" or any other code
  for a blank — a blank is information and is checked later.
- Watch the column alignment carefully: the day columns are narrow and evenly spaced,
  and shifting by one column corrupts the whole row. Use the numbered header as the
  ruler, and confirm the last code you emit really is under the LAST day column.
- Shaded/coloured cells (weekends are often tinted) still have a printed code — read
  the text, not the colour.

SELF-CHECK BEFORE YOU ANSWER
Each row usually has a printed "Leave Days" total. Count the leave-type codes you
transcribed for that row (L, AB, SL, UL and similar; HD counts as a half). If your count
does not match that row's printed total, re-read that row's cells before answering —
you have most likely mis-read or mis-aligned a cell.
Report what you counted in "leave_days_counted" either way; do NOT silently change cells
to force a match, and do not alter the printed total."""

GRID_OUTPUT = """Return EXACTLY this JSON and nothing else (no markdown fence):
{
  "rows": [
    {
      "sr_no": <integer, matching the row you were asked for>,
      "name": "<verbatim name on that row>",
      "days": {"1": "<code>", "2": "<code>", ..., "<N>": "<code>"},
      "leave_days_counted": <number you counted from the codes above>,
      "notes": "<anything unreadable about this row, else empty string>"
    }
  ]
}"""


def census_user_block(page_label: str) -> str:
    return (
        f"{CENSUS_USER_RULES}\n\n"
        f"This is {page_label}. List every employee row visible on it.\n\n"
        f"{CENSUS_OUTPUT}"
    )


def grid_user_block(
    wanted: list[tuple[int, str]], days_in_month: int, month_label: str,
) -> str:
    """`wanted` is the (sr_no, name) batch this call must transcribe."""
    listing = "\n".join(f"  - Sr No {sr}: {name}" for sr, name in wanted)
    return (
        f"{GRID_USER_RULES}\n\n"
        f"PERIOD: {month_label} — {days_in_month} calendar days, so every row's \"days\" "
        f"object must contain keys \"1\" through \"{days_in_month}\" and nothing else.\n\n"
        f"TRANSCRIBE EXACTLY THESE {len(wanted)} ROW(S), and only these:\n{listing}\n\n"
        f"{GRID_OUTPUT}"
    )
