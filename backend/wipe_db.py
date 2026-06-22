"""Wipe all application data (employees, inbox, timesheets, pipeline, etc.)."""
from __future__ import annotations

import asyncio

from sqlalchemy import text

from app.core.config import settings
from app.core.database import engine


async def clear_database() -> None:
    print(f"Connecting to database ({settings.database_url.split('://')[0]})...")
    async with engine.begin() as conn:
        if settings.database_url.startswith("sqlite"):
            await conn.execute(text("PRAGMA foreign_keys = OFF;"))
            result = await conn.execute(text(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';"
            ))
            tables = [row[0] for row in result]
            print(f"Found tables: {', '.join(tables)}")
            for table in tables:
                print(f"Clearing table: {table}")
                await conn.execute(text(f"DELETE FROM {table};"))
                try:
                    await conn.execute(text(f"DELETE FROM sqlite_sequence WHERE name='{table}';"))
                except Exception:
                    pass
            await conn.execute(text("PRAGMA foreign_keys = ON;"))
        else:
            result = await conn.execute(text(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public';"
            ))
            tables = [row[0] for row in result]
            if not tables:
                print("No tables found.")
                return
            print(f"Found tables: {', '.join(tables)}")
            quoted = ", ".join(f'"{t}"' for t in tables)
            print(f"Truncating: {quoted}")
            await conn.execute(text(f"TRUNCATE TABLE {quoted} RESTART IDENTITY CASCADE;"))
    print("Database cleared successfully.")


if __name__ == "__main__":
    asyncio.run(clear_database())
