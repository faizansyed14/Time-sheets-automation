"""Turn a parsed RosterDoc into staged review items — one per employee.

Every employee on the roster becomes their own PipelineFile row via the SAME
staging.stage_groups() call the Inbox/Upload/Portal paths use, all sharing
the one roster document as their raw copy. That is what gives the user's
requirement — "store this sheet for every employee who is on it" — for free:
at Accept, ingestion files each row's raw copy into that employee's own vault
folder, and because `source_kind` is "bulk" (not "email"), ingestion's
isolate_employee_attachments() deliberately does not fire, so the WHOLE
roster is filed for each person rather than a slice of it.

No file under services/extract_email or services/pipeline is modified.
"""
from __future__ import annotations

import hashlib

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.bulk_roster.roster_codes import distribute_days, verify_row_totals
from app.services.bulk_roster.roster_types import RosterDoc, RosterRow
from app.services.extract_email.constants import BUCKETS

SOURCE_KIND = "bulk"

# Words that, on the document's own title or a row's confirmation column,
# amount to the client stating this roster is approved. Conservative on
# purpose: a roster is a bulk submission, and over-claiming approval here
# would let auto_accept recommend filing something nobody signed off.
_APPROVAL_WORDS = ("approved", "approval", "signed off", "signoff", "authorised", "authorized")


def roster_digest(data: bytes) -> str:
    """Stable per-document id, so re-uploading the SAME roster updates the
    same staged rows (stage_groups matches on thread_key) instead of
    creating a second set for everyone on it."""
    return hashlib.sha256(data or b"").hexdigest()[:16]


def _row_approval(doc: RosterDoc, row: RosterRow) -> tuple[bool, str]:
    """(detected, detail) for ONE employee's row on the roster."""
    title = (doc.title or "").lower()
    confirm = (row.confirmation or "").strip()
    title_says = any(w in title for w in _APPROVAL_WORDS)
    row_says = any(w in confirm.lower() for w in _APPROVAL_WORDS)

    if row_says:
        return True, (f"Roster marks this row \"{confirm}\""
                      + (" on an approved monthly timesheet." if title_says else "."))
    if title_says and confirm:
        return True, (f"Roster is titled as an approved monthly timesheet; this row's "
                      f"confirmation column reads \"{confirm}\".")
    if title_says:
        return True, "Roster is titled as an approved monthly timesheet."
    if confirm:
        return False, (f"This row's confirmation column reads \"{confirm}\", which does not "
                       f"itself state manager approval — confirm in Review.")
    return False, "The roster carries no manager-approval evidence for this row."


async def build_groups(db: AsyncSession, doc: RosterDoc) -> tuple[list[dict], list[str]]:
    """RosterDoc -> the group dicts stage_groups() consumes.

    Returns (groups, unmatched_names). Identity is resolved against the HR
    master exactly as every other path does; a person who cannot be matched
    is NOT skipped and NOT guessed — they are staged with employee_pk=None so
    the reviewer picks them in Compare & Fix.
    """
    from app.models.employee import Employee
    from app.models.month_calendar import MonthCalendar
    from app.services.extract_email.grouping import normalise_sheet, tag_for
    from app.services.pipeline import matching

    month, year = int(doc.month), int(doc.year)
    cal_obj = (await db.execute(select(MonthCalendar).where(
        MonthCalendar.month == month, MonthCalendar.year == year,
    ))).scalar_one_or_none()
    cal_row = ({"weekend_weekdays": cal_obj.weekend_weekdays or [],
                "public_holidays": cal_obj.public_holidays or []} if cal_obj else None)

    # Fetched ONCE and reused for every row below — match_employee() would
    # otherwise re-fetch the whole employee table per roster row, which is
    # what made staging a large roster slow. Read-only for the life of this
    # call, so reusing it produces identical matches to fetching it fresh
    # each time — see matching.py's _match_by_name docstring.
    all_employees = (await db.execute(select(Employee))).scalars().all()

    doc_key = doc.title or ""
    groups: list[dict] = []
    unmatched: list[str] = []

    for row in doc.rows:
        fields, uncertain, day_issues = distribute_days(row.day_codes, month, year)
        total_issues = list(row.issues) + day_issues + verify_row_totals(
            row.day_codes, row.stated_leave_days, row.stated_billing_days, doc.calendar_days)

        m = await matching.match_employee(db, row.employee_id, row.name, all_employees=all_employees)
        if m.employee:
            employee_pk = m.employee.id
            matched_name = m.employee.name
            matched_id = m.employee.employee_id
            note = (f"From a shared roster of {doc.headcount} employees "
                    f"(row {row.sr_no}). {m.note or ''}").strip()
        else:
            employee_pk, matched_name, matched_id = None, row.name, row.employee_id
            unmatched.append(row.name)
            note = (f"From a shared roster of {doc.headcount} employees (row {row.sr_no}). "
                    f"Not in the matcher — roster says {row.name}"
                    + (f", {row.title}" if row.title else "")
                    + ". Pick the employee in Review.")

        approved, approval_detail = _row_approval(doc, row)
        working = fields.get("working_days", [])
        weekend = fields.get("weekend_days", [])
        leave_total = sum(len(fields.get(b, [])) for b in BUCKETS)

        sheet = {
            "name": f"{row.name} — row {row.sr_no}",
            "kind": "timesheet",
            "employee_name": row.name, "employee_id": row.employee_id,
            "month": month, "year": year,
            # Every calendar cell this row accounted for. The roster grid is
            # a whole-month form, so a complete read IS a full month.
            "days_covered": len(working) + len(weekend) + leave_total,
            "period_type": "full_month",
            "missing_days": [],
            "uncertain_days": uncertain,
            **{b: fields.get(b, []) for b in BUCKETS},
            "working_days": working, "weekend_days": weekend,
            "manager_signature": approved,
            "approval_evidence": approval_detail if approved else "",
            "approval_named_only": False,
            "text": "",
            "notes": "; ".join(total_issues[:4]),
        }
        ns = normalise_sheet(sheet, cal_row)
        issues = list(dict.fromkeys(total_issues + list(ns.get("_issues") or [])))

        groups.append({
            "tag": tag_for(f"{SOURCE_KIND}:{doc_key}:{row.sr_no}:{row.name}", month, year),
            "employee_pk": employee_pk, "name": matched_name, "employee_id": matched_id,
            "note": note,
            "month": month, "year": year,
            "sheets": [ns],
            "working_days": ns["working_days"], "weekend_days": ns["weekend_days"],
            "buckets": {b: ns[b] for b in BUCKETS},
            "uncertain_days": ns["uncertain_days"],
            "issues": issues,
            "missing_days": ns["missing_days"],
            "unaccounted_days": ns["_unaccounted_days"],
            "days_covered_total": ns.get("days_covered") or 0,
            "period_types": [ns["period_type"]] if ns.get("period_type") else [],
            "overlap_flags": [], "fold_notes": [],
            # Carried through for the staged row's own summary — not read by
            # stage_groups itself.
            "_roster_row": row.sr_no,
            "_approval": {"detected": approved, "detail": approval_detail},
        })

    return groups, unmatched


async def stage_roster(
    db: AsyncSession, *, filename: str, content_type: str, data: bytes, doc: RosterDoc,
) -> dict:
    """Stage every employee on the roster for review. Returns a summary dict."""
    from app.services.extract_email.staging import stage_groups

    groups, unmatched = await build_groups(db, doc)
    if not groups:
        raise ValueError("No employee rows could be staged from this roster.")

    digest = roster_digest(data)
    key = f"{SOURCE_KIND}:{digest}"

    # Document-level approval line shown on every staged row. Per-row
    # evidence still lives on each sheet; this is the one-line summary
    # staging writes into each item's own summary text.
    approved_rows = sum(1 for g in groups if g["_approval"]["detected"])
    approval = {
        "detected": approved_rows == len(groups) and approved_rows > 0,
        "detail": (
            f"Roster-level approval evidence found for {approved_rows} of {len(groups)} "
            f"employee(s)." if approved_rows else
            "No manager-approval evidence was found on this roster."),
    }

    staged = await stage_groups(
        db,
        source_kind=SOURCE_KIND,
        source_id=key,
        thread_key=key,
        raw_bytes=data,
        raw_name=filename,
        content_type=content_type or "application/octet-stream",
        groups=groups,
        approval=approval,
        run_meta={
            "method": doc.method,
            "model": doc.model,
            "calls": doc.llm_calls,
            "roster_headcount": doc.headcount,
            "roster_calendar_days": doc.calendar_days,
        },
    )

    flagged = sum(1 for g in groups if g["issues"])
    return {
        "staged": staged,
        "headcount": doc.headcount,
        "month": doc.month, "year": doc.year,
        "method": doc.method,
        "matched": len(groups) - len(unmatched),
        "unmatched": unmatched,
        "flagged": flagged,
        "issues": doc.issues,
    }
