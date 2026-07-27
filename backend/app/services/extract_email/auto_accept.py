"""AI auto-accept recommendation.

Ported from the prompt lab (docs/timesheet_strong_prompt.ipynb §11's
evaluate()). Recommendation only — nothing is ever filed here; a person still
presses Accept in Review.

No per-client-template restriction: the day-by-day accounting gate applies
uniformly to every document, the same way pass 2 reads every item the same
way regardless of layout. Recommends accepting only when EVERY check passes:

  1. Employee matched to a real person in the system (DB-backed fuzzy match
     in grouping.py — the notebook's flat name list was lab-only).
  2. Period (month + year) present.
  3. No validation issues — ISO/cross-bucket date conflicts (normalise_sheet)
     and any genuine cross-sheet conflict (e.g. two sheets both claiming the
     same full month).
  4. Full day-by-day coverage: every day placed into working/weekend/leave/
     uncertain, with zero days left unaccounted for. Leave-certificate-only
     groups are exempt from day-grid coverage — they were never meant to
     cover a whole month.
  5. No day flagged uncertain by the model.
  6. Any leave_certificate sheet in the group actually produced dates.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass, field

from app.services.extract_email.constants import BUCKETS


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
        reasons.append(f"period {calendar.month_name[group['month']]} {group['year']}")
    else:
        blockers.append("no month/year could be read")

    val_flags = (list(extra_flags or [])
                 + list(group.get("issues") or [])
                 + list(group.get("overlap_flags") or [])
                 + list(group.get("fold_notes") or []))
    # Complementary-merge notes are informational, not blockers.
    val_flags = [f for f in dict.fromkeys(val_flags)
                 if "merged for this month" not in f.lower()
                 and "leave is unioned" not in f.lower()
                 and "built from" not in f.lower()
                 and "will MERGE into it" not in f]
    if val_flags:
        blockers.append("validation flags: " + "; ".join(val_flags[:3]))
    else:
        reasons.append("validation clean")

    sheets = group.get("sheets") or []
    dim = sheets[0].get("_days_in_month") if sheets else 0
    is_cert_only = bool(sheets) and all(
        (s.get("period_type") == "partial" and int(s.get("days_covered") or 0) == 0)
        for s in sheets)

    if is_cert_only:
        reasons.append("leave-certificate-only group — day-grid coverage not required")
    else:
        dc_total = int(group.get("days_covered_total") or 0)
        missing = group.get("missing_days") or []
        unaccounted = group.get("unaccounted_days") or []
        if dim and dc_total < dim:
            blockers.append(f"incomplete coverage: {dc_total}/{dim} days")
        if missing:
            shown = ", ".join(str(d) for d in missing[:10]) + (" …" if len(missing) > 10 else "")
            blockers.append(f"missing days: {shown}")
        if unaccounted:
            shown = ", ".join(unaccounted[:10]) + (" …" if len(unaccounted) > 10 else "")
            blockers.append(
                f"day(s) not accounted for in any category (worked/weekend/leave/"
                f"uncertain): {shown}")
        if not (dim and dc_total < dim) and not missing and not unaccounted:
            reasons.append("full day-by-day coverage verified")

    uncertain = group.get("uncertain_days") or []
    if uncertain:
        examples = ", ".join(f"{u['date']} ({str(u.get('reason', ''))[:40]})" for u in uncertain[:5])
        blockers.append(
            f"{len(uncertain)} day(s) flagged uncertain by the model - needs manual "
            f"review: {examples}")

    for s in sheets:
        if (s.get("period_type") == "partial" and int(s.get("days_covered") or 0) == 0
                and not any(s.get(b) for b in BUCKETS)):
            # This condition isn't unique to leave certificates — a genuine
            # timesheet that read as entirely unusable (e.g. its date column
            # doesn't match its own stated month) hits it too, and calling
            # it a "leave certificate" then is actively misleading to the
            # reviewer about what kind of document actually failed.
            kind_label = "leave certificate" if s.get("kind") == "leave_certificate" else "timesheet"
            blockers.append(f"{kind_label} {s.get('name')} produced no dates")

    accepted = not blockers
    return AutoAcceptDecision(
        accepted=accepted,
        confidence="high" if accepted else "low",
        reasons=reasons,
        blockers=blockers,
    )
