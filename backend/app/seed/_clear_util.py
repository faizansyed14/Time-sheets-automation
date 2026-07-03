"""Shared helpers for scripts/db delete-* shell scripts."""
from __future__ import annotations

from sqlalchemy import delete, func, select

from app.core.database import SessionLocal
from app.models.employee import Employee


async def delete_all(model, label: str) -> dict[str, int]:
    async with SessionLocal() as db:
        n = (await db.execute(select(func.count()).select_from(model))).scalar_one()
        await db.execute(delete(model))
        await db.commit()
    return {label: n}


async def employee_count() -> int:
    async with SessionLocal() as db:
        return (await db.execute(select(func.count()).select_from(Employee))).scalar_one()


def report(counts: dict[str, int], done: str) -> None:
    for table, n in counts.items():
        print(f"  {table}: {n} row(s) deleted")
    print(done)
