"""
Seed all_employee_data from the demo employee matcher list (mock_data.EMPLOYEE_MATCHER).

Not run on startup — invoke manually:
    python seed_employees.py
    docker compose -f docker-compose.dev.yml exec backend python seed_employees.py
"""
from __future__ import annotations

import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import SessionLocal
from app.models.employee import Employee
from app.seed.mock_data import EMPLOYEE_MATCHER


async def seed_employee_matcher(db: AsyncSession) -> int:
    created = 0
    for employee_id, name, dco, manager, email, location in EMPLOYEE_MATCHER:
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


async def main() -> None:
    async with SessionLocal() as db:
        created = await seed_employee_matcher(db)
    print(f"Done — {created} demo employee(s) added (existing rows skipped).")


if __name__ == "__main__":
    asyncio.run(main())
