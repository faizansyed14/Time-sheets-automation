"""Shared "did the pipeline receive anything for this employee+period" query.

Used by the dashboard (employees.py's /coverage), the timesheet export
(timesheets.py), AND the reminder service (services/reminders/service.py) so
none of the three can ever drift apart — one definition of received/missing
everywhere it's shown, from whichever channel it arrived through (email,
Upload, Bulk Roster, Portal). This used to be scoped to source_kind="email"
only, back when email was the sole intake path — now that Upload/Bulk/Portal
are all real channels, that scoping was silently making the Dashboard and
Export show "Missing" for people who had, in fact, already submitted."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import ColumnElement, func, select
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
    """Distinct employee PKs the pipeline positively identified a sheet for,
    this month/year, from ANY intake channel (email, Upload, Bulk Roster,
    Portal) and regardless of final status — still awaiting Accept, already
    filed, or later flagged. Not source_kind-scoped: an employee who
    submitted via Upload or Bulk Roster must show as received/awaiting
    review, never as flat-out "missing", just because the channel wasn't
    email."""
    pk = staged_employee_pk()
    return (
        select(pk)
        .where(
            PipelineFile.month == month,
            PipelineFile.year == year,
            pk.is_not(None),
        )
        .distinct()
    )


async def received_employee_pks(db: AsyncSession, month: int, year: int) -> set[str]:
    """Materialised version of received_subq — for callers (like the export
    and the reminder service) that need to check membership for every
    employee in Python rather than compose it into a further SQL query."""
    rows = (await db.execute(received_subq(month, year))).scalars().all()
    return set(rows)


async def received_employee_dates(db: AsyncSession, month: int, year: int) -> dict[str, datetime]:
    """Employee PK -> the EARLIEST PipelineFile.created_at the pipeline has
    for them this month/year — "when the timesheet was received", for the
    export (a person can show up on more than one file in the same period,
    e.g. weekly sheets; the earliest one is what "received" should mean, not
    whichever happened to file last). Same identity source as received_subq
    (staged employee_pk), just keeping the timestamp instead of only
    membership."""
    pk = staged_employee_pk()
    rows = (
        await db.execute(
            select(pk, func.min(PipelineFile.created_at))
            .where(
                PipelineFile.month == month,
                PipelineFile.year == year,
                pk.is_not(None),
            )
            .group_by(pk)
        )
    ).all()
    return {row[0]: row[1] for row in rows}
