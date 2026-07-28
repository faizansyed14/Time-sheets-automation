"""
Deterministic validation of extracted leave buckets.

Produces the short, human-readable issues that drive green (verified) vs
yellow (manual_review). Checks:
  1. duplicate dates within a bucket
  2. the same date in more than one bucket
  3. dates outside the timesheet month
  4. header month/year vs the month/year the dates actually fall in
     (e.g. "February" written at the top but the rows are in January)
"""
from __future__ import annotations

import calendar
import datetime as dt
from collections import Counter

BUCKETS = ["annual", "remote", "sick", "maternity", "unpaid", "absent", "public_holiday", "other"]
# Not leave — day-accounting fields (worked / off) — but persisted, editable,
# and validated the same way as the leave buckets above.
DAY_FIELDS = ["working", "weekend"]
_LABEL = {
    "annual": "Annual leave", "remote": "Remote/WFH", "sick": "Sick leave",
    "maternity": "Maternity leave",
    "unpaid": "Unpaid leave", "absent": "Absent", "public_holiday": "Public holiday",
    "other": "Other leave",
    "working": "Working day", "weekend": "Weekend",
}


_SHORT = {
    "annual": "annual", "remote": "WFH", "sick": "sick", "maternity": "maternity",
    "unpaid": "unpaid", "absent": "absent", "public_holiday": "public holiday",
    "other": "other leave",
    "working": "working", "weekend": "weekend",
}

# A date legitimately belonging to BOTH categories in a pair is two true facts
# about one day, not a conflict — mirrors grouping.py's
# _COMPATIBLE_WITH_WORKING (a worked-remote day, or worked on a public
# holiday, is still a working day too).
_COMPATIBLE_PAIRS = {
    frozenset({"working", "remote"}),
    frozenset({"working", "public_holiday"}),
}


def _compatible(a: str, b: str) -> bool:
    return frozenset({a, b}) in _COMPATIBLE_PAIRS


def _parse(d: str) -> dt.date | None:
    try:
        return dt.date.fromisoformat(d)
    except Exception:
        return None


def _mname(month: int) -> str:
    return calendar.month_name[month] if 1 <= month <= 12 else str(month)


def summarize(cleaned: dict, flags: list[str], month: int, year: int, n_files: int = 1) -> str:
    """A clean, readable one-paragraph summary of a month's extraction.

    Always available (no LLM needed) and used as the fallback everywhere, so the
    record summary never degrades into a raw dump of dates. Example:
      "March 2026 — 3 annual, 1 sick, 1 WFH (5 days total). No issues found."
      "January 2026 — 4 annual (4 days total). 2 issues need review: Duplicate
       date 2026-01-13 listed twice in Annual leave. Header says February 2026,
       but the leave dates fall in January 2026."
    """
    parts = [f"{len(cleaned.get(b) or [])} {label}"
             for b, label in _SHORT.items() if cleaned.get(b)]
    total = sum(len(v or []) for v in (cleaned or {}).values())
    head = f"{_mname(month)} {year} — " + (", ".join(parts) if parts else "no leave recorded")
    head += f" ({total} day{'s' if total != 1 else ''} total"
    head += f", {n_files} files)." if n_files > 1 else ")."
    if not flags:
        return head + " No issues found — clean and ready for approval."
    n = len(flags)
    shown = " ".join(f.rstrip(".") + "." for f in flags[:6])
    more = f" (+{n - 6} more)" if n > 6 else ""
    verb = "needs" if n == 1 else "need"
    return f"{head} {n} issue{'s' if n != 1 else ''} {verb} review: {shown}{more}"


def validate(
    buckets: dict, month: int, year: int,
    header_month: int | None = None, header_year: int | None = None,
):
    flags: list[str] = []
    cleaned: dict[str, list[str]] = {}
    all_fields = BUCKETS + DAY_FIELDS

    # 1) within-bucket duplicates
    for b in all_fields:
        raw = buckets.get(b, []) or []
        seen, dupes, ordered = set(), set(), []
        for d in raw:
            if d in seen:
                dupes.add(d)
            else:
                seen.add(d)
                ordered.append(d)
        cleaned[b] = sorted(ordered)
        for d in sorted(dupes):
            flags.append(f"Duplicate date {d} listed twice in {_LABEL[b]}.")

    # 2) cross-bucket overlap — a date legitimately shared by a compatible
    # pair (working+remote, working+public_holiday) is two true facts about
    # one day, not a conflict; only a genuinely incompatible pair is flagged.
    by_date: dict[str, list[str]] = {}
    for b in all_fields:
        for d in cleaned[b]:
            by_date.setdefault(d, []).append(b)
    for d, keys in by_date.items():
        conflicting = [k for k in keys
                       if not all(_compatible(k, other) for other in keys if other != k)]
        if len(set(conflicting)) > 1:
            labels = [_LABEL[k] for k in dict.fromkeys(conflicting)]
            flags.append(f"Date {d} appears in multiple categories: {', '.join(labels)}.")

    # 3) out-of-month
    for b in all_fields:
        for d in cleaned[b]:
            pd = _parse(d)
            if pd and (pd.month != month or pd.year != year):
                flags.append(f"Date {d} is outside the timesheet month ({_mname(month)} {year}) in {_LABEL[b]}.")

    # 4) header month vs actual dates
    all_dates = [pd for b in all_fields for d in cleaned[b] if (pd := _parse(d))]
    if all_dates:
        cy, cm = Counter((pd.year, pd.month) for pd in all_dates).most_common(1)[0][0]
        if header_month and (header_month, header_year or year) != (cm, cy):
            flags.append(f"Header says {_mname(header_month)} {header_year or year}, "
                         f"but the leave dates fall in {_mname(cm)} {cy}.")
        elif (cm, cy) != (month, year):
            flags.append(f"Stated month is {_mname(month)} {year}, "
                         f"but most leave dates fall in {_mname(cm)} {cy}.")

    return cleaned, flags
