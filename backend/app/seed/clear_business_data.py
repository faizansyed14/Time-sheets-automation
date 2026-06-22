#!/usr/bin/env python3
"""
Remove all business data (employees, inbox, timesheets, pipeline) from the DB.
Keeps auth users (admin) and saved app config.

Run:
    python clear_business_data.py
    docker compose -f docker-compose.dev.yml exec backend python clear_business_data.py
"""
from __future__ import annotations

import asyncio

from sqlalchemy import delete, func, select

from app.core.database import SessionLocal
from app.models.email_message import EmailMessage
from app.models.employee import Employee
from app.models.pipeline_file import PipelineFile
from app.models.timesheet_record import TimesheetRecord


async def clear_business_data() -> dict[str, int]:
    """Delete business rows. Returns per-table delete counts."""
    counts: dict[str, int] = {}
    async with SessionLocal() as db:
        for label, model in (
            ("pipeline_files", PipelineFile),
            ("timesheet_records", TimesheetRecord),
            ("email_messages", EmailMessage),
            ("employees", Employee),
        ):
            n = (await db.execute(select(func.count()).select_from(model))).scalar_one()
            await db.execute(delete(model))
            counts[label] = n
        await db.commit()
    return counts


async def main() -> None:
    counts = await clear_business_data()
    for table, n in counts.items():
        print(f"  {table}: {n} row(s) deleted")
    print("Done — users and app config kept.")


if __name__ == "__main__":
    asyncio.run(main())
