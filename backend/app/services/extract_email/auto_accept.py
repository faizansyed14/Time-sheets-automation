"""AI auto-accept decision.

Extract Email normally STAGES a review item that a human accepts in Compare &
Fix. When the extraction is unambiguous and fully verifiable, that human step
is pure friction — so this engine decides, per employee+month group, whether it
is safe to file the record automatically ("Auto-accepted by AI").

# Auto-accept only when EVERY check passes:
#
#   1. Employee matched to a real person in the system (never a guess).
#   2. Period (month + year) is present.
#   3. The sheet is a RECOGNISED client template (see formats.py) — a known
#      layout we have format-specific extraction rules for.
#   4. Validation is clean — no duplicate/out-of-month/overlap/coverage flags.
#   5. Full-month day coverage is verifiable: it is a real daily grid AND every
#      working day is either worked (hours logged) or a recognised leave/holiday
#      — i.e. no row was silently missed.
#   6. Classifier did not flag the sheet as incomplete (dates_complete).
#
# Anything short of all six → held for human review, with the blocker reasons
# recorded so the reviewer sees exactly why the AI did not auto-accept.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AutoAcceptDecision:
    accepted: bool
    confidence: str                       # "high" | "low"
    reasons: list[str] = field(default_factory=list)    # why it COULD auto-accept
    blockers: list[str] = field(default_factory=list)   # why it could NOT

    def as_meta(self) -> dict:
        return {
            "accepted": self.accepted,
            "confidence": self.confidence,
            "reasons": self.reasons,
            "blockers": self.blockers,
        }


# A real daily attendance grid has at least this many days with logged hours —
# below it we cannot verify coverage, so we never auto-accept (hold for review).
_MIN_PRESENT_DAYS = 15


def _days_present_in_text(text: str, month: int) -> set[int]:
    """Day-of-month numbers that appear as a dated row for `month` in the sheet
    text — e.g. '1-June-26', '2- June -26', 'June 3'. Robust to PDF column
    splitting and 2-digit years (real ADR sheets print 'DD-Mon-YY'): it only
    needs each day token to appear, not a full parseable date."""
    import calendar
    import re as _re

    names = "|".join(_re.escape(n.lower()) for n in
                     (calendar.month_name[month], calendar.month_abbr[month]) if n)
    days: set[int] = set()
    for pat in (rf"\b(\d{{1,2}})\s*[-\s]\s*(?:{names})\b",   # 1-June / 1 June
                rf"\b(?:{names})\s*[-\s]\s*(\d{{1,2}})\b"):   # June-1 / June 1
        for m in _re.finditer(pat, text or "", _re.I):
            d = int(m.group(1))
            if 1 <= d <= 31:
                days.add(d)
    return days


def _coverage_blockers(group: dict) -> list[str]:
    """Verify the source sheet lists EVERY calendar day (no row was missing to
    extract from). We check the sheet's day tokens, not parsed dates, so it
    works with the 'DD-Mon-YY' 2-digit-year format real templates use and is
    immune to PDF column splitting. A full daily grid + clean validation is the
    auto-accept bar; anything missing days → hold for a human."""
    import calendar

    month, year = group.get("month"), group.get("year")
    if not (month and year):
        return ["no month/year to verify coverage against"]

    text = "\n".join(s.get("text") or "" for s in group.get("sheets", []))
    present = _days_present_in_text(text, month)
    last = calendar.monthrange(year, month)[1]
    if len(present) < _MIN_PRESENT_DAYS:
        return ["not a verifiable full-month daily grid "
                f"({len(present)} dated day-row(s) found) — needs a human check"]
    missing = [d for d in range(1, last + 1) if d not in present]
    if missing:
        shown = ", ".join(str(d) for d in missing[:8]) + (" …" if len(missing) > 8 else "")
        return [f"the sheet is missing rows for {len(missing)} day(s) of the month "
                f"({shown}) — needs a human check"]
    return []


def evaluate(group: dict, extra_flags: list[str] | None = None) -> AutoAcceptDecision:
    """Decide whether this employee+month group may be filed without review.

    `extra_flags` are the deterministic validation flags computed at staging
    time (duplicate dates, out-of-month dates, header mismatch) — any of them
    blocks auto-accept."""
    reasons: list[str] = []
    blockers: list[str] = []

    # 1. Employee matched to a real person.
    if group.get("employee_pk"):
        reasons.append(f"employee matched: {group.get('name') or '?'} "
                       f"({group.get('employee_id') or 'no id'})")
    else:
        blockers.append("employee is not matched to a person in the system")

    # 2. Period present.
    if group.get("month") and group.get("year"):
        import calendar
        reasons.append(f"period {calendar.month_name[group['month']]} {group['year']}")
    else:
        blockers.append("no month/year could be read")

    # 3. Recognised client template on a data sheet.
    from app.services.extract_email.formats import get_format
    fmts = [f for f in dict.fromkeys(
        s.get("format_id", "generic") for s in group.get("sheets", [])) if f != "generic"]
    if fmts:
        reasons.append("recognised template: "
                       + ", ".join(get_format(f).label for f in fmts))
    else:
        blockers.append("unrecognised timesheet template — no client format matched")

    # 4. Validation clean (staging flags + overlap + fold notes).
    val_flags = (list(extra_flags or [])
                 + list(group.get("overlap_flags") or [])
                 + list(group.get("fold_notes") or []))
    val_flags = list(dict.fromkeys(val_flags))
    if val_flags:
        blockers.append("validation flags: " + "; ".join(val_flags[:3]))
    else:
        reasons.append("validation clean")

    # 5. Full-month coverage verified.
    cov = _coverage_blockers(group)
    if cov:
        blockers.extend(cov)
    else:
        reasons.append("full-month day coverage verified")

    # 6. Classifier said the timesheet grid is incomplete — never auto-accept.
    incomplete = [
        s for s in group.get("sheets", [])
        if s.get("incomplete_sheet") or (
            s.get("kind") == "timesheet" and s.get("dates_complete") is False)
    ]
    if incomplete:
        names = ", ".join(s.get("name") or "?" for s in incomplete[:3])
        missing = []
        for s in incomplete:
            missing.extend(s.get("missing_days") or [])
        miss_txt = ""
        if missing:
            shown = ", ".join(str(d) for d in sorted(set(missing))[:8])
            miss_txt = f" (missing days: {shown})"
        blockers.append(
            f"classifier flagged incomplete day coverage on {names}{miss_txt}")
    else:
        reasons.append("classifier date-completeness ok")

    accepted = not blockers
    return AutoAcceptDecision(
        accepted=accepted,
        confidence="high" if accepted else "low",
        reasons=reasons,
        blockers=blockers,
    )
