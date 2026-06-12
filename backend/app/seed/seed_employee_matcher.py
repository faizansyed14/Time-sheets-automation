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
    for employee_id, name, dco, manager, email, location in EMPLOYEE_MATCHER:
        # Identity is (employee_id, name): AUH and DXB can share an ID.
        exists = (
            await db.execute(select(Employee).where(
                Employee.employee_id == employee_id, Employee.name == name))
        ).scalar_one_or_none()
        if exists:
            if not exists.location:
                exists.location = location
            continue
        db.add(Employee(
            employee_id=employee_id,
            name=name,
            dco_number=dco,
            account_manager=manager,
            employee_email_id=email,
            location=location,
        ))
        created += 1
    await db.commit()
    return created
