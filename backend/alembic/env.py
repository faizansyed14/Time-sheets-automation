"""
Alembic migration environment (async-aware).

Targets the SAME database the application uses. The URL is resolved (in
priority order) from:
    1. `alembic -x dburl=...`     one-off command-line override
    2. $ALEMBIC_DATABASE_URL      environment variable
    3. settings.database_url      the app's DATABASE_URL (.env / RDS endpoint)

Because the app uses an async driver (asyncpg), migrations run through an async
engine and `connection.run_sync(...)`. SQLite+aiosqlite is also supported (used
only to autogenerate a baseline without a live Postgres).

Common commands (run from backend/):
    alembic upgrade head                      # apply all migrations
    alembic downgrade -1                       # roll back one
    alembic revision --autogenerate -m "msg"   # create a new migration from models
    alembic stamp head                         # mark an existing DB as up-to-date
"""
from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import create_async_engine

# Import the app's metadata + register every model on Base.metadata.
from app.core.config import settings
from app.core.database import Base
import app.models  # noqa: F401  (side effect: registers all tables)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Autogenerate / compare against this metadata.
target_metadata = Base.metadata


def _database_url() -> str:
    """Resolve the target DB URL (CLI -x dburl=... > env var > app settings)."""
    x_args = context.get_x_argument(as_dictionary=True)
    return (
        x_args.get("dburl")
        or os.environ.get("ALEMBIC_DATABASE_URL")
        or settings.database_url
    )


def run_migrations_offline() -> None:
    """Emit SQL to stdout without a DB connection (`alembic upgrade --sql`)."""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Open an async connection and run migrations through run_sync."""
    connectable = create_async_engine(_database_url(), poolclass=pool.NullPool)
    async with connectable.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
