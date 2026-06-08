"""
One-off migration: add extended employee fields to all_employee_data.

Run once:  python -m app.migrations.add_employee_fields
Or it auto-runs on startup (safe if columns already exist).
"""
from __future__ import annotations

import asyncio
from sqlalchemy import text
from app.core.database import engine


COLUMNS = [
    ("project", "VARCHAR"),
    ("contact_no", "VARCHAR"),
    ("location", "VARCHAR"),
    ("all_emails", "VARCHAR"),
]


async def migrate():
    async with engine.begin() as conn:
        for col_name, col_type in COLUMNS:
            try:
                await conn.execute(text(
                    f"ALTER TABLE all_employee_data ADD COLUMN {col_name} {col_type}"
                ))
                print(f"  ✓ Added column: {col_name}")
            except Exception:
                # Column already exists — safe to ignore
                pass


if __name__ == "__main__":
    asyncio.run(migrate())
