"""Re-check employee identity for pipeline items staged before the employee
existed in the Employee Matcher (or that matched ambiguously at the time).

Matching only ever runs ONCE, at staging time (grouping.py / roster_stage.py).
There's no background job that revisits an already-staged item when a new
employee gets added to the matcher afterward — so a roster/email uploaded
just before a bulk employee import lands with real timesheets sitting
unmatched (employee_pk=None) indefinitely, which is exactly the case that
made "Mohammed Ali Hasan Ali Aldhaheri" show up as "missing" in Reminders
even though the file was already sitting in Pipeline.

This reuses the SAME matcher (services/pipeline/matching.py) against the
name/ID already captured at staging time — no re-extraction, no LLM call —
so it's safe and cheap to run over the whole backlog at once, unlike
/pipeline/{id}/retry (which re-runs the full vision extraction per file, and
isn't even built for bulk-roster items in the first place).
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.employee import Employee
from app.models.pipeline_file import PipelineFile, PipelineStatus
from app.services.pipeline import matching


async def rematch_unmatched(db: AsyncSession) -> dict:
    """Re-run matching.match_employee() for every still-under-review pipeline
    item whose staged employee_pk is None.

    Fills in employee_pk (plus the matched name/id, on the tracker row and
    inside extraction_meta) for whichever ones now resolve. Never touches an
    already-matched or already-decided (accepted/failed/resolved) item, and
    never guesses — anything still genuinely unmatched or ambiguous is left
    exactly as it was, still flagged for a human in Compare & Fix.
    """
    # Active only — a deactivated employee must never win a rematch over the
    # real, current person (see matching.py's _match_by_name docstring).
    all_employees = (await db.execute(
        select(Employee).where(Employee.active.is_(True))
    )).scalars().all()

    pk_path = PipelineFile.extraction_meta["staged"]["employee_pk"].as_string()
    rows = (await db.execute(
        select(PipelineFile).where(
            PipelineFile.status == PipelineStatus.NEEDS_REVIEW,
            pk_path.is_(None),
        )
    )).scalars().all()

    rematched: list[dict] = []
    still_unmatched: list[dict] = []

    for pf in rows:
        meta = pf.extraction_meta or {}
        staged = meta.get("staged")
        # Not every NEEDS_REVIEW row has this shape (e.g. a manual entry
        # carries a different extraction_meta entirely) — only a real
        # staged item with a genuinely blank employee_pk is ours to touch.
        if not staged or staged.get("employee_pk"):
            continue

        name = staged.get("matched_name")
        emp_id = staged.get("matched_employee_id")
        m = await matching.match_employee(db, emp_id, name, all_employees=all_employees)
        if m.employee:
            pf.employee_id = m.employee.employee_id
            pf.employee_name = m.employee.name
            pf.extraction_meta = {
                **meta,
                "staged": {
                    **staged,
                    "employee_pk": m.employee.id,
                    "matched_name": m.employee.name,
                    "matched_employee_id": m.employee.employee_id,
                },
            }
            rematched.append({
                "id": pf.id, "filename": pf.filename,
                "name": m.employee.name, "employee_id": m.employee.employee_id,
                "month": pf.month, "year": pf.year,
            })
        else:
            still_unmatched.append({
                "id": pf.id, "filename": pf.filename, "name": name,
                "month": pf.month, "year": pf.year,
            })

    await db.commit()
    return {
        "checked": len(rematched) + len(still_unmatched),
        "rematched_count": len(rematched),
        "rematched": rematched,
        "still_unmatched_count": len(still_unmatched),
        "still_unmatched": still_unmatched,
    }
