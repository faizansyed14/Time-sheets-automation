"""
In-place upgrade for EXISTING PostgreSQL databases (idempotent, best-effort):

  1. timesheet_records.source_files — per-file contributions so weekly / 15-day
     sheets merge into one monthly record.
  2. all_employee_data: employee_id is no longer globally unique (AUH and DXB
     share ID ranges) — drop the old single-column unique, add a plain index +
     a composite unique on (employee_id, name).

Runs automatically at startup. On a fresh database `create_all` already produces
the correct shape, so every statement here is wrapped to no-op if it doesn't
apply. For real production schema changes use Alembic.

Run manually:  python -m app.migrations.upgrade_v2
"""
from __future__ import annotations

import asyncio

from sqlalchemy import text

from app.core.database import engine


async def _exec(conn, sql: str) -> None:
    try:
        await conn.execute(text(sql))
    except Exception:
        pass


async def migrate() -> None:
    async with engine.begin() as conn:
        await _exec(conn, "ALTER TABLE timesheet_records ADD COLUMN IF NOT EXISTS source_files JSON")
        await _exec(conn, "ALTER TABLE all_employee_data DROP CONSTRAINT IF EXISTS "
                          "all_employee_data_employee_id_key")
        await _exec(conn, "DROP INDEX IF EXISTS ix_all_employee_data_employee_id")
        await _exec(conn, "CREATE INDEX IF NOT EXISTS ix_all_employee_data_employee_id "
                          "ON all_employee_data (employee_id)")
        await _exec(conn, "ALTER TABLE all_employee_data ADD CONSTRAINT uq_employee_id_name "
                          "UNIQUE (employee_id, name)")


if __name__ == "__main__":
    asyncio.run(migrate())
