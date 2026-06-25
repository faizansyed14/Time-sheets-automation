"""
Async database setup (SQLAlchemy 2.0, asyncpg/PostgreSQL).

The app targets PostgreSQL in every environment (dev, prod, tests). Switching to
a managed instance such as **AWS RDS** is purely a `DATABASE_URL` change in
`.env` — e.g.
    DATABASE_URL=postgresql+asyncpg://USER:PASS@my-db.xxxx.rds.amazonaws.com:5432/timesheet
No code changes are required.
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from app.core.config import settings


class Base(DeclarativeBase):
    pass


if settings.db_nullpool:
    engine = create_async_engine(
        settings.database_url, echo=False, future=True, poolclass=NullPool, pool_pre_ping=True,
    )
else:
    engine = create_async_engine(
        settings.database_url,
        echo=False,
        future=True,
        pool_pre_ping=True,      # drop dead connections (important for RDS/pooled)
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_recycle=1800,
    )
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db() -> AsyncSession:
    async with SessionLocal() as session:
        yield session


async def init_db() -> None:
    """Create tables if missing, and add any columns the models gained since the
    database was first created. For full schema changes in production use
    Alembic; this keeps the local quick-start (AUTO_CREATE_TABLES=true) working
    after a model adds a column to an existing table (e.g. the pipeline
    extraction-provenance columns) without a manual migration."""
    from app import models  # noqa: F401  (register models on Base.metadata)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_reconcile_missing_columns)


def _reconcile_missing_columns(sync_conn) -> None:
    """Add columns present in the models but missing on existing tables.

    Idempotent and additive only — never drops or alters existing columns. New
    columns are added NULLABLE (with a DEFAULT for booleans) so the ALTER is
    safe on tables that already hold rows."""
    from sqlalchemy import Boolean, inspect, text

    inspector = inspect(sync_conn)
    existing_tables = set(inspector.get_table_names())
    dialect = sync_conn.dialect

    for table in Base.metadata.sorted_tables:
        if table.name not in existing_tables:
            continue  # create_all already made it with every column
        have = {c["name"] for c in inspector.get_columns(table.name)}
        for col in table.columns:
            if col.name in have:
                continue
            coltype = col.type.compile(dialect=dialect)
            ddl = f'ALTER TABLE "{table.name}" ADD COLUMN IF NOT EXISTS "{col.name}" {coltype}'
            if isinstance(col.type, Boolean):
                ddl += " DEFAULT false"
            try:
                sync_conn.execute(text(ddl))
            except Exception:
                # Best-effort: a managed/Alembic-owned DB may forbid DDL here;
                # never block startup over an additive reconcile.
                pass

