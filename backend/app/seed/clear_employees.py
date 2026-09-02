#!/usr/bin/env python3
"""Delete all_employee_data (the employee matcher list).

Keeps timesheet_records / pipeline_files / reminder logs as-is — they carry
their own denormalized employee_id/employee_name fields, so history stays
readable even once the employees they reference are gone; matching just has
no one to resolve against until new employees are added back. Use
scripts/db/delete-records.sh / delete-pipeline.sh alongside this one if a
fuller wipe is what you actually want.

    bash scripts/db/delete-employees.sh
"""
from __future__ import annotations

import asyncio

from app.models.employee import Employee
from app.seed._clear_util import delete_all, report


async def main() -> None:
    counts = await delete_all(Employee, "all_employee_data")
    report(counts, "Done — employee matcher list cleared. Reload Employees.")


if __name__ == "__main__":
    asyncio.run(main())
