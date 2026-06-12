"""
FastAPI entrypoint.

Run (dev):  uvicorn app.main:app --reload --port 8000
Docs:       http://localhost:8000/docs
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.database import SessionLocal, init_db
from app.api.routes import employee_matcher, employees, files, inbox, pipeline, timesheets, upload


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    # Lightweight in-place upgrades for existing databases (new columns /
    # relaxed unique index). Safe to run repeatedly; use Alembic in production.
    try:
        from app.migrations.upgrade_v2 import migrate
        await migrate()
    except Exception:
        pass
    # Seed the demo employee matcher list only if the mock data module is present.
    # (Delete app/seed/mock_data.py + mock providers to remove mock entirely;
    #  this block then safely no-ops.)
    try:
        from app.seed.seed_employee_matcher import seed_employee_matcher
        async with SessionLocal() as db:
            await seed_employee_matcher(db)
    except Exception:
        pass
    yield


app = FastAPI(title=settings.app_name, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(inbox.router, prefix=settings.api_prefix)
app.include_router(timesheets.router, prefix=settings.api_prefix)
app.include_router(employees.router, prefix=settings.api_prefix)
app.include_router(employee_matcher.router, prefix=settings.api_prefix)
app.include_router(upload.router, prefix=settings.api_prefix)
app.include_router(files.router, prefix=settings.api_prefix)
app.include_router(pipeline.router, prefix=settings.api_prefix)


@app.get("/health")
async def health():
    return {"status": "ok", "email_provider": settings.email_provider,
            "extraction_engine": settings.extraction_engine}
