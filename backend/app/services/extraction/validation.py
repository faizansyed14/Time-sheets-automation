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


_SHORT = {
    "annual": "annual", "remote": "WFH", "sick": "sick",
    "unpaid": "unpaid", "absent": "absent", "public_holiday": "public holiday",
}


def _parse(d: str) -> dt.date | None:
    try:
        return dt.date.fromisoformat(d)
    except Exception:
        return None


def _mname(month: int) -> str:
    return calendar.month_name[month] if 1 <= month <= 12 else str(month)


def unaccounted_working_days(
    month: int, year: int, present: set[str], weekend_labeled: set[str], accounted: set[str],
) -> list[str]:
    """Weekdays (Mon–Fri, excluding public holidays / labelled weekends) that
    have NO hours logged and NO leave recorded — i.e. the employee neither
    worked nor took a recognised leave. Returns the ISO dates, sorted.

    Only meaningful for a real daily attendance grid, so the caller should
    require a reasonable number of `present` days before flagging."""
    if not (1 <= month <= 12 and year):
        return []
    last = calendar.monthrange(year, month)[1]
    out: list[str] = []
    for d in range(1, last + 1):
        date = dt.date(year, month, d)
        iso = date.isoformat()
        if date.weekday() >= 5:        # Saturday / Sunday
            continue
        if iso in weekend_labeled or iso in accounted or iso in present:
            continue
        out.append(iso)
    return out


def unaccounted_flag(dates: list[str]) -> str | None:
    """A concise, human review flag for unaccounted working days."""
    if not dates:
        return None
    def _fmt(iso: str) -> str:
        try:
            return dt.date.fromisoformat(iso).strftime("%d %b")
        except Exception:
            return iso
    shown = ", ".join(_fmt(d) for d in dates[:6]) + (" …" if len(dates) > 6 else "")
    n = len(dates)
    return (f"{n} working day{'s' if n != 1 else ''} have no hours and no leave recorded "
            f"({shown}) — please confirm whether these are unmarked absence or leave.")


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
