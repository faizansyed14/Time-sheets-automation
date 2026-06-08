"""
Seed all_employee_data from the mock employee matcher list.

Idempotent: running it twice won't create duplicates. Called on startup.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.employee import Employee
from app.seed.mock_data import EMPLOYEE_MATCHER


async def seed_employee_matcher(db: AsyncSession) -> int:
    created = 0
    for employee_id, name, dco, manager, email in EMPLOYEE_MATCHER:
        exists = (
            await db.execute(select(Employee).where(Employee.employee_id == employee_id))
        ).scalar_one_or_none()
        if exists:
            continue
        db.add(Employee(
            employee_id=employee_id,
            name=name,
            dco_number=dco,
            account_manager=manager,
            employee_email_id=email,
        ))
        created += 1
    await db.commit()
    return created
