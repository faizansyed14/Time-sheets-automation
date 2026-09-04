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


# --------------------------------------------------------------------------- #
# Single-call reader — a SEPARATE prompt pair from CENSUS/GRID above, not a
# variant of them. Deliberately kept apart so nothing here ever touches what
# CENSUS/GRID ask for or how they're read.
#
# Why this exists, and why it's opposite the census+grid split above: that
# split protects a LARGE roster from the "long structured reply starts
# silently skipping rows" failure (see the module intro). A SMALL, single-
# page roster doesn't carry that risk the same way, so it can be read in one
# call — census, full day grid, and approval sign-off together — instead of
# paying for a census call plus several grid-batch calls plus retries.
#
# NOTE: this reader currently sends the SAME image detail level as
# CENSUS/GRID above (not upgraded to high detail) — so it does NOT, on its
# own, fix small-code legibility (e.g. telling "VC" apart from a tick mark)
# any better than the census+grid reader already does. Its value here is
# purely fewer calls for a small roster; see git history / prior discussion
# if that legibility problem needs revisiting later.
#
# roster_extract.py tries this FIRST for a single-page vision-read roster;
# it never raises, and any imperfect/incomplete read falls straight back to
# the proven CENSUS/GRID reader above with zero effect on that path.
# --------------------------------------------------------------------------- #
SINGLE_CALL_SYSTEM = """You are reading, in ONE pass, a COMPLETE multi-employee monthly
attendance roster: one printed table where each ROW is a different person and the day
columns to the right are that person's calendar days — typically a tick/check mark for a
normal working day and a short code (letters, e.g. "H", "SL", "VC") for anything else,
though you must read whatever this specific sheet actually prints, verbatim.

List EVERY employee row, in printed order, with none skipped and none invented, and give
each one's COMPLETE day-by-day grid, from day 1 to the last day of the month. Missing a
person or a day is a serious error; inventing one that is not really there is equally
serious. If a specific cell is genuinely too unclear to call, say so rather than guess —
do not silently pick whichever code seems more likely."""

SINGLE_CALL_USER_RULES = """WHAT TO READ

Document header (once, from the title/top area):
  - month and year of the period, however it is printed
  - "Calendar Days: N" if printed
  - the agency/vendor name if printed

Approval sign-off (once, if this page shows it):
  - Look for an "Approved by" / sign-off line, usually near the bottom of the page.
  - Does it show an actual handwritten signature or stamp mark — as opposed to a
    blank line, dots/underscores with nothing on them, or only a printed name/title?
  - If this page has no such line at all, say so — do not guess "no signature" for a
    page that simply doesn't contain the sign-off area.

Per employee row, copy EXACTLY what is printed:
  - sr_no          the Sr No / S.No / serial number of the row, as an integer
  - name           the Resource Name / Employee Name, verbatim
  - title          the Title / Position / Designation column, verbatim, if present
  - location       the Loc. / Location column, if present
  - employee_id    ONLY if the roster actually has an employee-ID column; else null
  - confirmation   the "Emp. Timesheet Confirmation" / status column, if present
  - leave_days     the number printed in a "Leave Days" column (number, not text), else null
  - billing_days   the number printed in a "Billing Days" column, else null
  - days           one code per calendar day, 1 through the last day of the month —
                    see HOW TO READ A ROW'S DAY CELLS below

HOW TO READ A ROW'S DAY CELLS
- Work left to right across every day column, using the numbered header as the ruler.
- Emit one entry per calendar day from 1 to the stated number of days, with no gaps.
- Copy the code verbatim — a literal tick/check mark, a letter code, whatever this
  sheet actually prints. Do not translate a tick mark into a word, and do not decide
  what a code MEANS — that interpretation happens in a later step, not here.
- A genuinely EMPTY cell is "" (empty string). Do NOT substitute a tick mark or any
  other code for a blank — a blank is information and is checked later. Likewise never
  substitute a blank for a cell that DOES have a mark, even a faint one.
- Shaded/coloured cells (weekends are often tinted) still have their own printed mark —
  read the mark, not the colour.
- If the sheet prints a Key/Legend for its codes, that legend is authoritative for what
  a code MEANS on this specific document — but your job here is still to copy the code
  exactly as printed, not to translate it using the legend.

RULES
- Copy names character-for-character. Do not correct spelling, expand initials, reorder
  first/last, or tidy capitalisation.
- A name that wraps onto two printed lines is still ONE employee.
- If a column does not exist on this roster, use null. Never invent a value.
- Do not merge two people who share a name — they are separate rows with separate
  sr_no values."""

SINGLE_CALL_OUTPUT = """Return EXACTLY this JSON and nothing else (no markdown fence):
{
  "month": <1-12 or null>,
  "year": <4-digit year or null>,
  "calendar_days": <integer or null>,
  "agency": "<string or null>",
  "approval_signature": {
    "present": <true if this page shows an actual signature/stamp on an "Approved by"
                line, false if that line is blank/unsigned, null if this page has no
                such line at all>,
    "detail": "<one short phrase describing what you see, or null>"
  },
  "employees": [
    {
      "sr_no": <integer>,
      "name": "<verbatim>",
      "title": "<string or null>",
      "location": "<string or null>",
      "employee_id": "<string or null>",
      "confirmation": "<string or null>",
      "leave_days": <number or null>,
      "billing_days": <number or null>,
      "days": {"1": "<code>", "2": "<code>", ..., "<N>": "<code>"}
    }
  ],
  "rows_visible": <integer: how many employee rows you could see in total>
}"""


def single_call_user_block() -> str:
    return f"{SINGLE_CALL_USER_RULES}\n\n{SINGLE_CALL_OUTPUT}"


# --------------------------------------------------------------------------- #
# Attendance-report reader — a THIRD, separate prompt pair, for a different
# roster SHAPE entirely: one row per employee PER DAY (grouped into date
# sections), with punch times instead of a single per-day code, and a
# "Reason" annotation printed as a floating label near a specific row on the
# original page (see roster_extract._looks_like_attendance_report /
# _vision_attendance_report). Deliberately kept apart from CENSUS/GRID/
# SINGLE_CALL above — nothing here touches what those ask for.
#
# TEXT input, not an image: a vision read of this report's rendered page was
# tried first and came back empty (the model reported seeing zero rows on a
# page that renders perfectly legibly to a person) — this report has a real
# embedded text layer, and reading that directly (reconstructed row-by-row
# by word position, so a Reason annotation lands on the SAME line as the row
# it sits next to — see roster_extract._attendance_report_page_texts) is
# both cheaper and the version already proven to work, since the app's
# generic chat/"Ask AI" feature reads PDFs the identical way.
#
# Kept narrow on purpose either way: the model's ONLY job is to TRANSCRIBE
# what is printed — it is never asked to decide what a code or a Reason
# MEANS for timesheet purposes. That classification happens afterward, in
# code (roster_codes.translate_attendance_report_row), so it stays auditable
# and testable independent of any model call, and the model has nothing to
# hallucinate a judgment about.
# --------------------------------------------------------------------------- #
ATTENDANCE_REPORT_SYSTEM = """You are reading the extracted TEXT of one page of a
multi-employee daily attendance report. Each line below is one visual row from the
original page, reconstructed from the page's real text by vertical position — columns
within a row are separated by TAB characters, in left-to-right order.

The page is organised into DATE SECTIONS (a line like "01/07/2026 (Wednesday)"), and
under each date section is a block of employee rows — one row per employee for that
date. The same employees repeat under every date section on the page.

Some rows carry an extra piece of text sharing that row's line — a "Reason" annotation
(e.g. "Time off personal", "Sick leave", "Annual Leave"). This was a floating label
positioned next to that specific row on the original page, and it landed on the same
line here because it shares that row's vertical position — it is not a normal table
column, but it DOES belong to the row it appears on.

Your ONLY job is to TRANSCRIBE exactly what is printed for every row — not to decide
what any of it means. Do not translate a code into a leave type, do not decide whether
someone was "really" present or absent, and do not invent a Reason for a row that has
none. If a value is genuinely blank, report it as blank/null — never guess a value to
fill a gap.

The single most important requirement: list EVERY employee row under EVERY date section
in this text, with none skipped and none invented. Missing a row is a serious error;
inventing one that is not really there is equally serious."""

ATTENDANCE_REPORT_USER_RULES = """WHAT TO READ, per employee row under a date section:
  - employee_id   the User ID / Employee ID printed on the row (e.g. "FAC13763")
  - name          the employee's name, verbatim
  - date          the date this row's date SECTION header shows, as YYYY-MM-DD
  - in_time       the FIRST IN time printed on the row, else null
  - out_time      the FIRST OUT time printed on the row, else null
  - half1         the "1st Half" column's code exactly as printed (e.g. "AB", "PR", "WO"),
                   else null
  - half2         the "2nd Half" column's code exactly as printed, else null
  - reason        the Reason annotation sharing THIS row's line, verbatim, else null if
                   this row's line has no such extra text on it

EACH FIELD HAS A SPECIFIC SHAPE — use it to tell fields apart when a row's columns don't
line up cleanly (e.g. because some columns are blank, which shifts what looks adjacent):
  - in_time / out_time are ALWAYS either a clock time like "07:45" or "15:30", or blank/
    null. NEVER put a word, a leave phrase, or a short code like "AB"/"WO" into these
    fields, even if it appears in the position where a time would normally sit.
  - half1 / half2 are ALWAYS a short 2-3 letter code (e.g. "AB", "PR", "WO"), or blank/
    null. Never a clock time, never a multi-word phrase.
  - reason is the ONLY field that holds free-form, multi-word text (e.g. "Sick leave",
    "Time off personal", "Annual Leave", "Forget to stamp out"). If you see text like
    that anywhere on a row's line, it belongs in reason — even if it sits in a position
    where a time or a half-code would normally be, because the row's real time/code is
    blank that day.

RULES
- Copy names and IDs character-for-character. Do not correct, expand, or reorder them.
- Do not let "1st Half"/"2nd Half" change what you report for in_time/out_time or
  reason — report each field exactly as printed, independently of the others.
- The column headers ("User ID", "Name", "Shift", "IN-SPFID", ...) and the legend line
  ("SPFID: 1=Official IN, ...") are not employee rows — never transcribe those as a row.
- Every date section's employee list is usually the SAME people as every other date
  section on the page — if a section appears to be missing someone the others have,
  double-check before leaving them out; but never invent a row for a date section that
  genuinely doesn't show that person (e.g. someone who joined partway through)."""

ATTENDANCE_REPORT_OUTPUT = """Return EXACTLY this JSON and nothing else (no markdown fence):
{
  "rows": [
    {
      "employee_id": "<string or null>",
      "name": "<verbatim>",
      "date": "<YYYY-MM-DD>",
      "in_time": "<HH:MM or null>",
      "out_time": "<HH:MM or null>",
      "half1": "<string or null>",
      "half2": "<string or null>",
      "reason": "<string or null>"
    }
  ],
  "rows_visible": <integer: how many employee-rows (across every date section) you could
                   find in total in this text>
}"""


def attendance_report_user_block(page_label: str, page_text: str) -> str:
    return (
        f"{ATTENDANCE_REPORT_USER_RULES}\n\n"
        f"This is {page_label}. Transcribe every employee row under every date section "
        f"in the text below.\n\n"
        f"--- BEGIN EXTRACTED TEXT ---\n{page_text}\n--- END EXTRACTED TEXT ---\n\n"
        f"{ATTENDANCE_REPORT_OUTPUT}"
    )
