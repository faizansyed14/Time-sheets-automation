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

BUCKETS = ["annual", "remote", "sick", "unpaid", "absent", "public_holiday"]
_LABEL = {
    "annual": "Annual leave", "remote": "Remote/WFH", "sick": "Sick leave",
    "unpaid": "Unpaid leave", "absent": "Absent", "public_holiday": "Public holiday",
}


def _parse(d: str) -> dt.date | None:
    try:
        return dt.date.fromisoformat(d)
    except Exception:
        return None


def _mname(month: int) -> str:
    return calendar.month_name[month] if 1 <= month <= 12 else str(month)


def validate(
    buckets: dict, month: int, year: int,
    header_month: int | None = None, header_year: int | None = None,
):
    flags: list[str] = []
    cleaned: dict[str, list[str]] = {}

    # 1) within-bucket duplicates
    for b in BUCKETS:
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

    # 2) cross-bucket overlap
    by_date: dict[str, list[str]] = {}
    for b in BUCKETS:
        for d in cleaned[b]:
            by_date.setdefault(d, []).append(_LABEL[b])
    for d, labels in by_date.items():
        if len(labels) > 1:
            flags.append(f"Date {d} appears in multiple categories: {', '.join(labels)}.")

    # 3) out-of-month
    for b in BUCKETS:
        for d in cleaned[b]:
            pd = _parse(d)
            if pd and (pd.month != month or pd.year != year):
                flags.append(f"Date {d} is outside the timesheet month ({_mname(month)} {year}) in {_LABEL[b]}.")

    # 4) header month vs actual dates
    all_dates = [pd for b in BUCKETS for d in cleaned[b] if (pd := _parse(d))]
    if all_dates:
        cy, cm = Counter((pd.year, pd.month) for pd in all_dates).most_common(1)[0][0]
        if header_month and (header_month, header_year or year) != (cm, cy):
            flags.append(f"Header says {_mname(header_month)} {header_year or year}, "
                         f"but the leave dates fall in {_mname(cm)} {cy}.")
        elif (cm, cy) != (month, year):
            flags.append(f"Stated month is {_mname(month)} {year}, "
                         f"but most leave dates fall in {_mname(cm)} {cy}.")

    return cleaned, flags
