"""
In-place upgrade for existing databases (v2 features):

  1. timesheet_records.source_files  — per-file contributions so weekly /
     15-day sheets merge into one monthly record.
  2. all_employee_data: employee_id is no longer globally unique (AUH and DXB
     share ID ranges) — drop the old unique index, add a plain index and a
     composite unique on (employee_id, name).

Runs automatically at startup; every statement is individually best-effort so
it is safe on a fresh DB (where create_all already produced the new shape).
Use Alembic for real production migrations.

Run manually:  python -m app.migrations.upgrade_v2
"""
from __future__ import annotations

import asyncio

from sqlalchemy import text

from app.core.database import engine


async def _exec(conn, sql: str) -> bool:
    try:
        await conn.execute(text(sql))
        return True
    except Exception:
        return False


async def migrate() -> None:
    async with engine.begin() as conn:
        # 1) new column on timesheet_records
        await _exec(conn, "ALTER TABLE timesheet_records ADD COLUMN source_files JSON")

        # 2) relax the unique on employee_id (name differs across AUH/DXB)
        is_sqlite = engine.url.get_backend_name() == "sqlite"
        if is_sqlite:
            await _exec(conn, "DROP INDEX IF EXISTS ix_all_employee_data_employee_id")
            await _exec(conn,
                        "CREATE INDEX IF NOT EXISTS ix_all_employee_data_employee_id "
                        "ON all_employee_data (employee_id)")
            await _exec(conn,
                        "CREATE UNIQUE INDEX IF NOT EXISTS uq_employee_id_name "
                        "ON all_employee_data (employee_id, name)")
        else:  # postgres
            await _exec(conn, "ALTER TABLE all_employee_data DROP CONSTRAINT IF EXISTS "
                              "all_employee_data_employee_id_key")
            await _exec(conn, "DROP INDEX IF EXISTS ix_all_employee_data_employee_id")
            await _exec(conn, "CREATE INDEX IF NOT EXISTS ix_all_employee_data_employee_id "
                              "ON all_employee_data (employee_id)")
            await _exec(conn, "ALTER TABLE all_employee_data ADD CONSTRAINT uq_employee_id_name "
                              "UNIQUE (employee_id, name)")


if __name__ == "__main__":
    asyncio.run(migrate())
