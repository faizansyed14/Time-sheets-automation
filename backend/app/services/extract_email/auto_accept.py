"""AI auto-accept recommendation.

Extract Email stages every group for human Review. When extraction is
unambiguous and fully verifiable, this engine marks the group with a
recommendation to accept — but nothing is filed until a person presses
Accept in Review.

# Recommendation only when EVERY check passes:
#
#   1. Employee matched to a real person in the system (never a guess).
#   2. Period (month + year) is present.
#   3. Every sheet in the group is the ADR format specifically
#      (alpha_adr_attendance) — the one template this has been verified
#      against closely enough to trust unattended. Any other recognised
#      template, any leave_certificate/approval-only group, or anything the
#      model could not confidently identify is held for review regardless of
#      how clean the rest of the checks look.
#   4. Validation is clean — no duplicate/out-of-month/overlap flags.
#   5. Full-month coverage — trust pass-2 LLM counts (days_covered,
#      missing_days, period_type), NOT regex on parser text. ADR dates vary
#      ('1 June 2026', '01/Aug/2025', '1-June-26', …) so code cannot parse them
#      reliably. Multiple weekly/partial sheets for one month are merged first;
#      coverage is judged on the GROUP.
#   6. Pass 2 did not report incomplete coverage on the merged group.
#   7. Any leave_certificate sheet in the group has extracted dates.
#
# Anything short of all six → held for review without the recommendation,
# with blocker reasons so the reviewer sees exactly why.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# The only template auto-accept currently trusts unattended. Every other
# recognised format, and anything generic/unidentified, is always held for a
# human — widen this set only after the same scrutiny ADR got.
_AUTO_ACCEPT_FORMATS = frozenset({"alpha_adr_attendance"})


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


# Below this the model clearly did not see a real daily grid.
_MIN_PRESENT_DAYS = 15


def merged_coverage(group: dict) -> dict:
    """Aggregate pass-2 coverage fields across all timesheets in one group.

    One full-month sheet → use its counts. Several week/partial sheets for the
    same employee+month → sum days_covered, union missing_days; complementary
    partials merge into one month (NOT a duplicate).
    """
    import calendar

    month, year = group.get("month"), group.get("year")
    last = calendar.monthrange(year, month)[1] if month and year else 0
    timesheets = [s for s in group.get("sheets", []) if s.get("kind") == "timesheet"]

    if not timesheets:
        return {"days_covered": 0, "missing_days": [], "period_type": "unknown",
                "dates_complete": False, "sheet_count": 0}

    if len(timesheets) == 1:
        s = timesheets[0]
        return {
            "days_covered": int(s.get("days_covered") or 0),
            "missing_days": list(s.get("missing_days") or []),
            "period_type": s.get("period_type") or "unknown",
            "dates_complete": bool(s.get("dates_complete")),
            "sheet_count": 1,
        }

    total_dc = sum(int(s.get("days_covered") or 0) for s in timesheets)
    missing: set[int] = set()
    for s in timesheets:
        missing |= {int(d) for d in (s.get("missing_days") or [])
                      if isinstance(d, int) or (isinstance(d, str) and str(d).isdigit())}

    full_claims = [s for s in timesheets if s.get("period_type") == "full_month"]
    # Two+ sheets each claiming full_month → real duplicate; caller handles via overlap_flags.
    if len(full_claims) >= 2:
        return {
            "days_covered": total_dc,
            "missing_days": sorted(missing),
            "period_type": "partial",
            "dates_complete": False,
            "sheet_count": len(timesheets),
            "duplicate_full_months": True,
        }

    if len(full_claims) == 1:
        s = full_claims[0]
        return {
            "days_covered": int(s.get("days_covered") or 0),
            "missing_days": list(s.get("missing_days") or []),
            "period_type": "full_month",
            "dates_complete": bool(s.get("dates_complete")),
            "sheet_count": len(timesheets),
        }

    # Complementary week/partial/half-month sheets — merged coverage.
    dates_complete = total_dc >= last and not missing
    return {
        "days_covered": total_dc,
        "missing_days": sorted(missing),
        "period_type": "full_month" if dates_complete else "partial",
        "dates_complete": dates_complete,
        "sheet_count": len(timesheets),
        "complementary": True,
    }


def _coverage_blockers(group: dict) -> list[str]:
    """Trust pass-2 LLM day counts — not regex on parser text."""
    import calendar

    month, year = group.get("month"), group.get("year")
    if not (month and year):
        return ["no month/year to verify coverage against"]

    cov = group.get("coverage") or merged_coverage(group)
    last = calendar.monthrange(year, month)[1]
    dc = int(cov.get("days_covered") or 0)
    missing = list(cov.get("missing_days") or [])

    if cov.get("duplicate_full_months"):
        return ["two sheets both claim the same full month — needs a human check"]

    if dc < _MIN_PRESENT_DAYS:
        n = cov.get("sheet_count", 1)
        return [f"model reports only {dc} dated day-row(s) across {n} sheet(s) "
                f"— needs a human check"]

    if missing:
        shown = ", ".join(str(d) for d in missing[:8]) + (" …" if len(missing) > 8 else "")
        return [f"model reports {len(missing)} missing day(s) of the month ({shown})"]

    if cov.get("period_type") == "full_month" or cov.get("dates_complete"):
        return []

    if dc >= last:
        return []

    return [f"model reports partial month coverage ({dc}/{last} day-rows)"]


def _leave_cert_blockers(group: dict) -> list[str]:
    """Hold when pass 1 found leave evidence but pass 2 extracted no dates."""
    from app.services.extract_email.constants import BUCKETS

    blockers: list[str] = []
    for s in group.get("sheets", []):
        if s.get("kind") != "leave_certificate":
            continue
        buckets = s.get("buckets") or {}
        total = sum(len(buckets.get(b) or []) for b in BUCKETS)
        if total == 0:
            name = s.get("name") or "leave certificate"
            blockers.append(f"leave evidence ({name}) had no dates extracted")
    return blockers


def evaluate(group: dict, extra_flags: list[str] | None = None) -> AutoAcceptDecision:
    """Decide whether this employee+month group may be filed without review."""
    reasons: list[str] = []
    blockers: list[str] = []

    if group.get("employee_pk"):
        reasons.append(f"employee matched: {group.get('name') or '?'} "
                       f"({group.get('employee_id') or 'no id'})")
    else:
        blockers.append("employee is not matched to a person in the system")

    if group.get("month") and group.get("year"):
        import calendar
        reasons.append(f"period {calendar.month_name[group['month']]} {group['year']}")
    else:
        blockers.append("no month/year could be read")

    from app.services.extract_email.formats import get_format
    fmts = [f for f in dict.fromkeys(
        s.get("format_id", "generic") for s in group.get("sheets", [])) if f != "generic"]
    if fmts and all(f in _AUTO_ACCEPT_FORMATS for f in fmts):
        reasons.append("recognised template: "
                       + ", ".join(get_format(f).label for f in fmts))
    elif fmts:
        blockers.append(
            "auto-accept is limited to ADR timesheets for now — this group is "
            + ", ".join(get_format(f).label for f in fmts))
    else:
        blockers.append("unrecognised timesheet template — no client format matched")

    val_flags = (list(extra_flags or [])
                 + list(group.get("overlap_flags") or [])
                 + list(group.get("fold_notes") or []))
    # Complementary merge notes are informational — not blockers.
    val_flags = [f for f in dict.fromkeys(val_flags)
                 if "merged for this month" not in f.lower()
                 and "leave is unioned" not in f.lower()
                 and "built from" not in f.lower()
                 and "will MERGE into it" not in f]
    if val_flags:
        blockers.append("validation flags: " + "; ".join(val_flags[:3]))
    else:
        reasons.append("validation clean")

    cov_blockers = _coverage_blockers(group)
    if cov_blockers:
        blockers.extend(cov_blockers)
    else:
        cov_meta = group.get("coverage") or merged_coverage(group)
        if cov_meta.get("complementary"):
            reasons.append(f"full-month coverage verified across "
                           f"{cov_meta.get('sheet_count')} merged sheet(s)")
        else:
            reasons.append("full-month day coverage verified (model)")

    cov_meta = group.get("coverage") or merged_coverage(group)
    if cov_meta.get("dates_complete") or (
            cov_meta.get("complementary") and not cov_meta.get("missing_days")):
        reasons.append("model date-completeness ok")
    elif cov_meta.get("duplicate_full_months"):
        pass  # already in cov_blockers
    elif not cov_blockers:
        blockers.append("model flagged incomplete day coverage on merged group")

    cert_blockers = _leave_cert_blockers(group)
    if cert_blockers:
        blockers.extend(cert_blockers)

    accepted = not blockers
    return AutoAcceptDecision(
        accepted=accepted,
        confidence="high" if accepted else "low",
        reasons=reasons,
        blockers=blockers,
    )
