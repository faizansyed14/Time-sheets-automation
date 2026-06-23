#!/usr/bin/env python3
"""
Prepare the database for Alembic, then apply pending migrations.

Databases created before Alembic adoption (SQLAlchemy create_all) already have
the baseline tables but no alembic_version row. Detect that case and stamp
0001_baseline before upgrade head so startup does not fail with DuplicateTable.

Used by Docker compose backend command and scripts/db/migrate.sh.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect

from app.core.config import settings
from app.core.database import engine

BASELINE_REVISION = "0001_baseline"
_LEGACY_MARKERS = ("all_employee_data", "auth_users", "email_messages")


def _alembic_ini() -> Config:
    ini = Path(__file__).resolve().parent / "alembic.ini"
    return Config(str(ini))


async def _legacy_schema_without_alembic() -> bool:
    async with engine.connect() as conn:
        def check(sync_conn) -> bool:
            names = set(inspect(sync_conn).get_table_names())
            if "alembic_version" in names:
                return False
            return any(t in names for t in _LEGACY_MARKERS)

        return await conn.run_sync(check)


def main() -> None:
    legacy = asyncio.run(_legacy_schema_without_alembic())
    cfg = _alembic_ini()
    if legacy:
        print(
            f"▶ Legacy schema detected (tables exist, no Alembic revision) "
            f"— stamping {BASELINE_REVISION}",
            flush=True,
        )
        command.stamp(cfg, BASELINE_REVISION)
    print("▶ alembic upgrade head", flush=True)
    command.upgrade(cfg, "head")
    print("✓ database is at the latest migration", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"✗ migration bootstrap failed: {exc}", file=sys.stderr, flush=True)
        raise SystemExit(1) from exc
