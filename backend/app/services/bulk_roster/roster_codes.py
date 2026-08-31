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

# Roster cell code -> where that day belongs in the system's own day model
# (services/extract_email/constants.py: BUCKETS + DAY_FIELDS).
#
# Codes are matched case-insensitively after stripping punctuation/space.
# The legend on the sample sheet reads:
#     P - Present | AB - Absent | HD - Half Day | WK - Weekend
#     L - Leave   | PH - Public Holiday
CODE_TO_FIELD: dict[str, str] = {
    "P": "working_days",
    "PRESENT": "working_days",
    "WK": "weekend_days",
    "W": "weekend_days",
    "WE": "weekend_days",
    "WEEKEND": "weekend_days",
    "OFF": "weekend_days",
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
    "AL": "annual",
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
}

# Codes that count toward the roster's own "Leave Days" total. Everything
# else (present, weekend, public holiday) is billable/non-leave, which is
# what makes `Leave Days + Billing Days = Calendar Days` hold on the sample.
_LEAVE_CODES = {"L", "LV", "LEAVE", "AL", "SL", "SICK", "UL", "LWP", "UNPAID",
                "ML", "MATERNITY", "AB", "A", "ABS", "ABSENT"}
# Counted as HALF a leave day by the roster's arithmetic.
_HALF_DAY_CODES = {"HD", "HALF", "HALFDAY"}

# A leave bucket a reviewer should confirm, because the roster's own notation
# was ambiguous about which kind of leave it meant.
AMBIGUOUS_LEAVE_CODES = {"L", "LV", "LEAVE"}


def normalise_code(raw: str | None) -> str:
    """A cell's text -> a lookup key. Blank/dash/NA all collapse to ""."""
    s = "".join(ch for ch in str(raw or "").strip().upper() if ch.isalnum())
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
        issues.append(
            f"{ambiguous} day(s) marked only as \"L\" — the roster doesn't say which kind of "
            f"leave, so they were recorded as annual; confirm or re-bucket in Review")

    half = sum(1 for c in day_codes.values() if normalise_code(c) in _HALF_DAY_CODES)
    if half:
        issues.append(
            f"{half} half-day (HD) cell(s) — a record stores whole days only, so these were "
            f"recorded as 'other' leave; confirm the intended split in Review")

    return issues


def _num(v: float) -> str:
    """5.0 -> '5', 4.5 -> '4.5' — totals read naturally in a message."""
    f = float(v)
    return str(int(f)) if f.is_integer() else str(f)
