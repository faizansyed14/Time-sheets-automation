#!/usr/bin/env python3
"""Delete timesheet_records. bash scripts/db/delete-records.sh"""
from __future__ import annotations

import asyncio

from app.models.timesheet_record import TimesheetRecord
from app.seed._clear_util import delete_all, employee_count, report


async def main() -> None:
    counts = await delete_all(TimesheetRecord, "timesheet_records")
    report(counts, f"  employees: {await employee_count()} row(s) kept (not cleared)")
    print("Done — timesheet records cleared. Reload Review.")


if __name__ == "__main__":
    asyncio.run(main())
