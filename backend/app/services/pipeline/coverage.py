"""Shared "did the pipeline receive anything for this employee+period" query.

Used by both the dashboard (employees.py's /coverage) and the timesheet
export (timesheets.py) so the two can never drift apart — same definition of
received/missing everywhere it's shown.
"""
from __future__ import annotations

from sqlalchemy import ColumnElement, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.pipeline_file import PipelineFile


def staged_employee_pk() -> ColumnElement[str]:
    """The employee PK the pipeline resolved a staged group to, read out of
    PipelineFile.extraction_meta.staged.employee_pk. Set at staging time
    (extract_email/staging.py) for every group it stages and left untouched
    by Accept, so it identifies a sheet's employee however far that item got:
    still awaiting accept, already filed, or later flagged."""
    return PipelineFile.extraction_meta["staged"]["employee_pk"].as_string()


def received_subq(month: int, year: int):
    """Distinct employee PKs the pipeline positively identified from an
    EMAILED sheet for this month/year, regardless of final status."""
    pk = staged_employee_pk()
    return (
        select(pk)
        .where(
            PipelineFile.source_kind == "email",
            PipelineFile.month == month,
            PipelineFile.year == year,
            pk.is_not(None),
        )
        .distinct()
    )


async def received_employee_pks(db: AsyncSession, month: int, year: int) -> set[str]:
    """Materialised version of received_subq — for callers (like the export)
    that need to check membership for every employee in Python rather than
    compose it into a further SQL query."""
    rows = (await db.execute(received_subq(month, year))).scalars().all()
    return set(rows)
