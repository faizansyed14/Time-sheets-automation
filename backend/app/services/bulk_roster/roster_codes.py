"""Day-code interpretation and the per-row checksum.

This module is where "100% accurate" actually gets earned. A vision model
reading a 31-column grid for 100 people WILL occasionally misread a cell, and
no amount of prompt wording changes that. What makes the result trustworthy
is that this style of roster prints its own arithmetic on every row:

    Leave Days + Billing Days = Calendar Days

So the extracted grid is not taken on faith — it is reconciled against the
numbers the document itself states. A row whose grid disagrees with its own
printed totals is FLAGGED and blocked from auto-accept rather than filed, so
a wrong read surfaces as "check this one" instead of as silently bad data.
"""
from __future__ import annotations

import calendar as _calendar
import re as _re

# Roster cell code -> where that day belongs in the system's own day model
# (services/extract_email/constants.py: BUCKETS + DAY_FIELDS).
#
# Codes are matched case-insensitively after stripping punctuation/space.
# Different clients print different legends — this table is the UNION of
# every one seen so far, not just one sample sheet:
#     P - Present | AB - Absent | HD - Half Day | WK - Weekend
#     L - Leave   | PH - Public Holiday
#     (PGC "Certificate of Attendance" style) VC - Annual Leave (their own
#     term for it) | H - Public Holiday | SL - Sick Leave | NS - National
#     Service | a literal tick mark (see _CHECKMARK_CHARS below) - Present
CODE_TO_FIELD: dict[str, str] = {
    "P": "working_days",
    "PRESENT": "working_days",
    "WK": "weekend_days",
    "W": "weekend_days",
    "WE": "weekend_days",
    "WEEKEND": "weekend_days",
    "OFF": "weekend_days",
    "WO": "weekend_days",  # "Weekly Off" — FAZAA-style attendance reports
    "PH": "public_holiday",
    "H": "public_holiday",
    "HOL": "public_holiday",
    "HOLIDAY": "public_holiday",
    "AB": "absent",
    "A": "absent",
    "ABS": "absent",
    "ABSENT": "absent",
    # A bare "L" says a day was leave but NOT which kind. The roster format
    # simply does not carry that distinction, so it lands in `annual` (by far
    # the most common) and every affected row is flagged for a reviewer to
    # confirm or re-bucket in Compare & Fix. Guessing silently would be worse
    # than saying so.
    "L": "annual",
    "LV": "annual",
    "LEAVE": "annual",
    # AL/AUL in the PGC-style legend are "Approved Leave"/"Unapproved Leave" —
    # an approval STATUS, not a leave type. Per this client, both file as
    # annual (like VC, their actual "Annual Leave" code) and are always
    # flagged for confirmation — see AMBIGUOUS_LEAVE_CODES below.
    "AL": "annual",
    "AUL": "annual",
    "VC": "annual",     # "Vacation"/"Annual Leave" in the PGC-style legend
    "SL": "sick",
    "SICK": "sick",
    "UL": "unpaid",
    "LWP": "unpaid",
    "UNPAID": "unpaid",
    "ML": "maternity",
    "MATERNITY": "maternity",
    "WFH": "remote",
    "R": "remote",
    "REMOTE": "remote",
    # Half day: the record model stores date LISTS, with no notion of a
    # fraction, so a half day cannot be represented faithfully in any leave
    # bucket. It goes to `other` and is always flagged — see _HALF_DAY_CODES.
    "HD": "other",
    "HALF": "other",
    "HALFDAY": "other",
    # National Service has no dedicated bucket either — same "other" +
    # always-flag treatment as a half day, see the NS check in
    # verify_row_totals(). Never silently folded into "absent"/"unpaid" —
    # it's neither of those in reality.
    "NS": "other",
}

# Codes that count toward the roster's own "Leave Days" total. Everything
# else (present, weekend, public holiday) is billable/non-leave, which is
# what makes `Leave Days + Billing Days = Calendar Days` hold on the sample.
_LEAVE_CODES = {"L", "LV", "LEAVE", "AL", "AUL", "VC", "SL", "SICK", "UL", "LWP", "UNPAID",
                "ML", "MATERNITY", "AB", "A", "ABS", "ABSENT", "NS"}
# Counted as HALF a leave day by the roster's arithmetic.
_HALF_DAY_CODES = {"HD", "HALF", "HALFDAY"}

# A leave bucket a reviewer should confirm, because the roster's own notation
# was ambiguous about which kind of leave it meant: L/LV/LEAVE say "leave"
# without a type at all, and AL/AUL/VC (the PGC-style legend) say only an
# approval status or a client-specific term — none of them are as
# self-evident as SL/ML/UL, so all get a mandatory "confirm in Review" flag
# even though a default bucket (annual) is filled in.
AMBIGUOUS_LEAVE_CODES = {"L", "LV", "LEAVE", "AL", "AUL", "VC"}

# Any of these glyphs, however a vision model chooses to transcribe a ticked
# checkbox, means "present" — normalised straight to "P" below. Without this,
# a checkmark strips to "" under the alnum-only cleanup (it has no letters or
# digits) and gets silently mistaken for a genuinely BLANK cell — on a
# tick-mark roster (e.g. PGC's "Certificate of Attendance") that misreads
# every present day as an "uncertain_days" blank, which is exactly the
# failure this table exists to prevent.
_CHECKMARK_CHARS = {"✓", "✔", "√", "☑"}


def normalise_code(raw: str | None) -> str:
    """A cell's text -> a lookup key. Blank/dash/NA all collapse to "".
    A checkmark glyph collapses to "P" (present) instead — see
    _CHECKMARK_CHARS above for why that has to happen BEFORE the alnum
    strip, not after."""
    s = str(raw or "").strip()
    if s in _CHECKMARK_CHARS:
        return "P"
    s = "".join(ch for ch in s.upper() if ch.isalnum())
    if s in ("", "-", "NA", "N/A", "NIL", "NONE"):
        return ""
    return s


def is_known_code(raw: str | None) -> bool:
    return normalise_code(raw) in CODE_TO_FIELD


def leave_weight(raw: str | None) -> float:
    """How much this code contributes to the roster's stated Leave Days."""
    code = normalise_code(raw)
    if code in _HALF_DAY_CODES:
        return 0.5
    return 1.0 if code in _LEAVE_CODES else 0.0


# --------------------------------------------------------------------------- #
# Long-format (one row per employee PER DAY, e.g. "PGC"-style attendance
# exports) AttendanceType phrases -> the SAME short codes CODE_TO_FIELD
# already understands above. Kept as its OWN table (not merged into
# CODE_TO_FIELD) because the vocabulary is completely different in shape —
# full English phrases, not single letters/short codes — and mixing the two
# risks an accidental key collision.
#
# Only genuinely unambiguous day types are listed. A phrase not listed here is
# deliberately left UNTRANSLATED by translate_attendance_type() below, so it
# flows into day_codes unchanged, fails the CODE_TO_FIELD lookup exactly like
# any other unrecognised code, and becomes an uncertain_days entry — reaching
# a human reviewer instead of being guessed. Extending coverage later is a
# one-line addition here, never a structural change.
# --------------------------------------------------------------------------- #
LONG_FORMAT_BASE_TYPE_TO_CODE: dict[str, str] = {
    "daily present": "P",
    "weekend": "WK",
    "sick leave": "SL",
    "annual leave": "AL",
}


def _split_status_suffix(raw: str) -> tuple[str, str]:
    """"Sick Leave - Waiting for Approval -   Line Manager" ->
    ("sick leave", "waiting for approval -   line manager"). A base type is
    everything before the FIRST " - "; the rest is a workflow-status suffix
    the source system appends, not part of the type itself."""
    base, _, suffix = raw.strip().partition(" - ")
    return base.strip().lower(), suffix.strip().lower()


def translate_attendance_type(raw: str | None) -> tuple[str, str | None]:
    """One long-format cell's AttendanceType text -> (code, flag_message).

    `code` is one of CODE_TO_FIELD's own short codes when the base type is
    known; otherwise it's the ORIGINAL raw text, unchanged, so the normal
    normalise_code()+CODE_TO_FIELD lookup in distribute_days() naturally
    treats it as an unrecognised code (uncertain_days, blocks auto-accept)
    with zero new code needed there.

    `flag_message`, when not None, is a business-status note (e.g. "not yet
    approved") to append to the row's issues — distinct from a parse
    failure: the type WAS understood, but its approval workflow isn't done.
    """
    text = str(raw or "").strip()
    if not text:
        return "", None
    base, suffix = _split_status_suffix(text)
    code = LONG_FORMAT_BASE_TYPE_TO_CODE.get(base)
    if code is None:
        return text, None
    if suffix and ("waiting" in suffix or "pending" in suffix):
        return code, f"marked \"{text}\" — not yet approved, confirm before filing"
    return code, None


# --------------------------------------------------------------------------- #
# FAZAA-style "Organization-Wise Attendance" reports: one row per employee PER
# DAY (grouped into date sections), carrying punch times, an unreliable
# "1st Half"/"2nd Half" AB/PR/WO notation, and a "Reason" annotation the
# reader sees inline with a specific row's own text (see roster_extract.
# _read_attendance_page / _vision_attendance_report). The bucket decision
# below is made entirely in CODE, never by the model — the model's only job
# is to transcribe what's printed; classifying it is deterministic and
# testable independent of any model call.
#
# The client's own printed legend (report footer "Notes"):
#   Time off - Personal : leave approved by management for personal matters
#   Time off - Business : leave approved by management for work purposes
#                          (meetings, etc.)
#   Event Leave          : leave approved for an official company event
# None of these map onto an EXISTING bucket (they are neither annual, sick,
# nor unpaid specifically) — deliberately left UNTRANSLATED below, same as an
# unrecognised AttendanceType above: it flows through as an unrecognised code
# -> uncertain_days, so a reviewer picks the right bucket rather than one
# being guessed. "Forget to stamp out" is not a leave type at all (the
# employee worked; a punch was simply missed) — same treatment.
ATTENDANCE_REPORT_REASON_TO_CODE: dict[str, str] = {
    "sick leave": "SL",
    # Reused even though "AL" is in AMBIGUOUS_LEAVE_CODES (forcing a mandatory
    # confirm-in-Review flag elsewhere for a genuinely ambiguous approval-
    # status code) — deliberately conservative for a brand-new, unproven
    # format: extra scrutiny on every Annual Leave day here is the safer
    # default until this reader has a track record, even though FAZAA's own
    # "Annual Leave" text is not actually ambiguous the way AL/VC are for
    # other clients.
    "annual leave": "AL",
}

# A real punch time always looks like this — one or two digits, a colon, two
# digits ("7:45", "07:45"). Anything else in an in_time/out_time field is
# NOT a real time, even if it's a non-null string — confirmed necessary
# against real model output: the reader occasionally misattributes free-text
# (e.g. "Sick leave") into the in_time/out_time slots when a row's real
# times are blank, and treating any non-null string there as "they punched
# in and out" would silently file a full-day absence as a normal working
# day. Validating the SHAPE, not just presence, closes that.
_TIME_RE = _re.compile(r"^\d{1,2}:\d{2}$")


def _looks_like_time(v: str | None) -> bool:
    return bool(v) and bool(_TIME_RE.match(str(v).strip()))


def _is_wo(v: str | None) -> bool:
    return normalise_code(v) == "WO"


def translate_attendance_report_row(
    in_time: str | None, out_time: str | None,
    half1: str | None, half2: str | None, reason: str | None,
) -> str:
    """One employee-day of a FAZAA-style attendance report -> a single short
    code, in the SAME vocabulary CODE_TO_FIELD already understands — so
    distribute_days()/verify_row_totals() need ZERO changes to handle this
    format, exactly like translate_attendance_type() above does for the PGC
    long-format.

    Deliberately does NOT trust the printed "1st Half"/"2nd Half" AB/PR
    codes as a presence signal — confirmed against real client data that AB
    can appear on a row that ALSO has full punch times and a full Work Hrs
    total (this report's own AB/PR notation does not reliably mean
    absent/present the way it does on other rosters, and the client
    themselves does not treat it as authoritative). The only signals trusted
    here: did the employee actually punch in AND out with values that look
    like real times (wins regardless of what AB/PR says), then WO (an
    unambiguous weekly-off marker — checked in in_time/out_time too, since a
    blank-time row can shift which field the reader puts "WO" into), then
    the Reason text attached to this row."""
    if _looks_like_time(in_time) and _looks_like_time(out_time):
        return "P"
    if _is_wo(in_time) or _is_wo(out_time) or _is_wo(half1) or _is_wo(half2):
        return "WO"
    reason_key = str(reason or "").strip().lower()
    return ATTENDANCE_REPORT_REASON_TO_CODE.get(reason_key, str(reason or "").strip())


# --------------------------------------------------------------------------- #
# HHRC-style "Outsourced Staff Attendance Report" exports: a genuinely clean
# XLSX (real typed datetime/time cells, not scanned text), long-format (one
# row per employee PER DAY) with an explicit Type + Sub Type pair instead of
# a single code — Type is "Normal"/"Week End"/"On Leave"/"Personal"/
# "Official", Sub Type refines it ("ANNUAL LEAVE", "Managed by Missed
# Swipe") when Type alone isn't specific enough. See roster_parse.
# _parse_hhrc_format for how a file lands here — zero LLM calls, same as the
# PGC long-format reader, since this is exact cell data, not an image/PDF.
#
# The one real wrinkle this format has that PGC/wide don't: SOME employees
# have MORE THAN ONE ROW for the same calendar day — confirmed real cases
# are a short "Personal" break (a few minutes) or an "Official" half-day,
# always seen ALONGSIDE a "Normal" row covering the rest of that day (e.g. a
# 2-minute personal break, then Normal 8:32-16:00). A genuine "Normal"
# segment always wins that day's bucket outright, regardless of what else
# shares the day, because it means the employee demonstrably worked. Never
# observed in real data: a whole day that is ONLY "Personal"/"Official"
# with no "Normal" row at all — rather than guess what that would mean, it
# is left unmapped so it surfaces as an uncertain_days entry for review.
HHRC_SUBTYPE_TO_CODE: dict[str, str] = {
    # Reused despite "AL" being in AMBIGUOUS_LEAVE_CODES (mandatory confirm-
    # in-Review elsewhere) — same deliberately conservative choice as the
    # FAZAA reader above: extra scrutiny on a brand-new, unproven format is
    # the safer default, even though this Sub Type spells out "ANNUAL LEAVE"
    # unambiguously.
    "annual leave": "AL",
}


def translate_hhrc_day(entries: list[tuple[str | None, str | None]]) -> str:
    """`entries` is every (Type, Sub Type) pair recorded for ONE employee on
    ONE calendar day — usually exactly one, occasionally more (see above).
    Returns a single short code in CODE_TO_FIELD's own vocabulary, so
    distribute_days()/verify_row_totals() need ZERO changes to handle this
    format, exactly like translate_attendance_type()/
    translate_attendance_report_row() above do for their own formats."""
    types = [(t or "").strip().lower() for t, _ in entries]
    if "normal" in types:
        return "P"
    if len(entries) == 1:
        typ, subtype = entries[0]
        typ_norm = (typ or "").strip().lower()
        if typ_norm == "week end":
            return "WK"
        if typ_norm == "on leave":
            sub_norm = (subtype or "").strip().lower()
            return HHRC_SUBTYPE_TO_CODE.get(sub_norm, str(subtype or typ or "").strip())
        # "Personal"/"Official" alone, no Normal row that day — never seen
        # in real data; surface exactly what was printed rather than guess.
        return str(subtype or typ or "").strip()
    # More than one row, none of them Normal, more than one distinct Type —
    # also never seen in real data; surface the combination, don't guess.
    return " + ".join(sorted({(t or "").strip() for t, _ in entries if t}))


def iso(year: int, month: int, day: int) -> str:
    return f"{year:04d}-{month:02d}-{day:02d}"


def days_in_month(month: int, year: int) -> int:
    return _calendar.monthrange(year, month)[1]


def distribute_days(
    day_codes: dict[int, str], month: int, year: int,
) -> tuple[dict[str, list[str]], list[dict], list[str]]:
    """Turn {day number -> code} into the system's own day model.

    Returns (fields, uncertain_days, issues) where `fields` maps every
    bucket/day-field name to its ISO date list.

    A day whose code is blank or unrecognised is NEVER quietly dropped and
    never guessed into a bucket — it becomes an `uncertain_days` entry, which
    is exactly the signal auto_accept already blocks on, so the row reaches a
    human instead of being filed with a hole in it.
    """
    dim = days_in_month(month, year)
    fields: dict[str, list[str]] = {}
    uncertain: list[dict] = []
    issues: list[str] = []
    unknown_seen: set[str] = set()

    for day in sorted(day_codes):
        if not (1 <= int(day) <= dim):
            # A fixed 1-31 form used for a 30-day month prints trailing
            # columns that do not exist. Only complain when such a column
            # actually carried a value.
            if normalise_code(day_codes[day]):
                issues.append(
                    f"day {day} is outside {_calendar.month_name[month]} {year} "
                    f"({dim} days) — ignored")
            continue

        raw = day_codes[day]
        code = normalise_code(raw)
        date = iso(year, month, int(day))

        if not code:
            uncertain.append({"date": date, "reason": "blank cell on the roster"})
            continue
        field = CODE_TO_FIELD.get(code)
        if field is None:
            unknown_seen.add(str(raw).strip())
            uncertain.append({"date": date, "reason": f"unrecognised code {str(raw).strip()!r}"})
            continue
        fields.setdefault(field, []).append(date)

    # Every calendar day must be present in the grid at all — a column the
    # reader never saw is as much a gap as a blank one.
    missing = [d for d in range(1, dim + 1) if d not in day_codes]
    for d in missing:
        uncertain.append({"date": iso(year, month, d), "reason": "no column read for this day"})
    if missing:
        issues.append(f"{len(missing)} day column(s) were not read: "
                      + ", ".join(str(d) for d in missing[:10])
                      + (" …" if len(missing) > 10 else ""))
    if unknown_seen:
        issues.append("unrecognised day code(s): " + ", ".join(sorted(unknown_seen)[:8]))

    return fields, uncertain, issues


def verify_row_totals(
    day_codes: dict[int, str],
    stated_leave: float | None,
    stated_billing: float | None,
    calendar_days: int | None,
) -> list[str]:
    """Reconcile the extracted grid against the roster's OWN printed totals.

    This is the accuracy backbone described in the module docstring. Any
    disagreement is returned as an issue string; the caller puts those on the
    staged row, where they both show in Compare & Fix and block auto-accept.
    An empty list means the document's own arithmetic confirms the read.
    """
    issues: list[str] = []

    # Some roster styles (e.g. PGC's "Certificate of Attendance") print no
    # Leave Days / Billing Days total at all — every check below this point
    # is then a no-op, and a row with no unknown codes would otherwise sail
    # through with an EMPTY issues list, as if it had been cross-checked and
    # confirmed, when nothing was actually verified. Say so explicitly rather
    # than let a format with no arithmetic to check against look "clean".
    if stated_leave is None and stated_billing is None:
        issues.append(
            "this roster prints no Leave/Billing-day totals to check the day grid "
            "against — nothing here has been independently verified; review the "
            "day-by-day cells directly before accepting")

    counted = sum(leave_weight(c) for c in day_codes.values())

    if stated_leave is not None and abs(counted - float(stated_leave)) > 1e-6:
        issues.append(
            f"leave-day mismatch: the roster states {_num(stated_leave)} leave day(s) "
            f"but {_num(counted)} were read from the day grid — check this row")

    # Second, independent check on the SAME row: the document's two totals
    # must themselves add up. When they don't, the sheet is internally
    # inconsistent and no extraction of it can be trusted either way.
    if (stated_leave is not None and stated_billing is not None and calendar_days):
        total = float(stated_leave) + float(stated_billing)
        if abs(total - float(calendar_days)) > 1e-6:
            issues.append(
                f"the roster's own totals don't add up: {_num(stated_leave)} leave + "
                f"{_num(stated_billing)} billing = {_num(total)}, not {calendar_days} calendar days")

    ambiguous = sum(1 for c in day_codes.values() if normalise_code(c) in AMBIGUOUS_LEAVE_CODES)
    if ambiguous:
        codes_seen = sorted({str(c).strip() for c in day_codes.values()
                             if normalise_code(c) in AMBIGUOUS_LEAVE_CODES})
        quoted = "/".join(f'"{c}"' for c in codes_seen)
        issues.append(
            f"{ambiguous} day(s) marked only as {quoted} — the roster doesn't say which "
            f"specific kind of leave this is (or, for an approval-status code like AL/AUL, "
            f"only whether it was approved), so they were recorded as annual; confirm or "
            f"re-bucket in Review")

    half = sum(1 for c in day_codes.values() if normalise_code(c) in _HALF_DAY_CODES)
    if half:
        issues.append(
            f"{half} half-day (HD) cell(s) — a record stores whole days only, so these were "
            f"recorded as 'other' leave; confirm the intended split in Review")

    ns_days = sum(1 for c in day_codes.values() if normalise_code(c) == "NS")
    if ns_days:
        issues.append(
            f"{ns_days} National Service (NS) day(s) — there is no dedicated bucket for this, "
            f"so they were recorded as 'other' leave; confirm in Review")

    return issues


def _num(v: float) -> str:
    """5.0 -> '5', 4.5 -> '4.5' — totals read naturally in a message."""
    f = float(v)
    return str(int(f)) if f.is_integer() else str(f)
