#!/usr/bin/env python3
"""Seed exactly two test employees, both routing to the requesting user's own
inboxes — for safely exercising reminders/OTP/export end-to-end without any
risk of a real employee ever being touched. Idempotent (matches
seed_employee_matcher.py's own skip-if-exists pattern): safe to run again
after scripts/db/delete-employees.sh without creating duplicates.

    bash scripts/db/seed-test-employees.sh
"""
from __future__ import annotations

import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import SessionLocal
from app.models.employee import Employee

TEST_EMPLOYEES = [
    # (employee_id, name, work_email, personal_email)
    ("TEST-FAIZAN-1", "faizan", "faizan@alpha.ae", "syedfaizan062@gmail.com"),
    ("TEST-FAIZAN-2", "faizan2", None, "syedfaizanuddin143@gmail.com"),
]


async def seed_test_employees(db: AsyncSession) -> int:
    created = 0
    for employee_id, name, work_email, personal_email in TEST_EMPLOYEES:
        exists = (
            await db.execute(select(Employee).where(
                Employee.employee_id == employee_id, Employee.name == name))
        ).scalar_one_or_none()
        if exists:
            exists.work_email = work_email
            exists.personal_email = personal_email
            exists.employee_email_id = work_email or personal_email
            continue
        db.add(Employee(
            employee_id=employee_id,
            name=name,
            account_manager="Test Manager",
            location="DXB",
            work_email=work_email,
            personal_email=personal_email,
            employee_email_id=work_email or personal_email,  # same fallback import_service.py uses
        ))
        created += 1
    await db.commit()
    return created


async def main() -> None:
    async with SessionLocal() as db:
        created = await seed_test_employees(db)
    print(f"Done — {created} new test employee(s) added (existing ones updated in place).")


if __name__ == "__main__":
    asyncio.run(main())
