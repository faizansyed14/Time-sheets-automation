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
    """Create tables if missing. For schema changes in production use Alembic."""
    from app import models  # noqa: F401  (register models on Base.metadata)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
