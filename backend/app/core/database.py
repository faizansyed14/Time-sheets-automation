"""
Async database setup (SQLAlchemy 2.0 style).

Uses SQLite + aiosqlite by default for zero-dependency local runs.
The session/engine wiring is identical for Postgres — only the URL changes.
"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings


class Base(DeclarativeBase):
    pass


# Ensure the sqlite directory exists before the engine connects.
if settings.database_url.startswith("sqlite"):
    db_file = settings.database_url.split("///")[-1]
    Path(db_file).parent.mkdir(parents=True, exist_ok=True)

engine = create_async_engine(settings.database_url, echo=False, future=True)
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db() -> AsyncSession:
    async with SessionLocal() as session:
        yield session


async def init_db() -> None:
    """Create tables. In production use Alembic migrations instead."""
    # Import models so they register on Base.metadata.
    from app import models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
