"""
FastAPI entrypoint.

Run (dev):  uvicorn app.main:app --reload --port 8000
Docs:       http://localhost:8000/docs

App (business) routes require an authenticated user; /admin requires the admin
role; /auth and /health are public. Set AUTH_ENABLED=false to disable the gate
for local hacking.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.deps import require_user
from app.api.routes import (
    admin,
    auth,
    employee_matcher,
    employees,
    files,
    inbox,
    pipeline,
    timesheets,
    upload,
)
from app.core.config import settings
from app.core.database import SessionLocal, init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    try:
        from app.migrations.upgrade_v2 import migrate
        await migrate()
    except Exception:
        pass
    try:
        from app.services.pipeline.ingestion import relocate_legacy_pipeline_raw
        relocate_legacy_pipeline_raw()
    except Exception:
        pass
    async with SessionLocal() as db:
        # default admin + apply any saved AI config to the live process
        try:
            from app.seed.seed_admin import seed_admin
            await seed_admin(db)
        except Exception:
            pass
        try:
            from app.services.config.service import load_and_apply
            await load_and_apply(db)
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
    expose_headers=["X-Captcha-Id"],
)

# public
app.include_router(auth.router, prefix=settings.api_prefix)
# admin (admin role enforced inside the router)
app.include_router(admin.router, prefix=settings.api_prefix)

# business routes — require an authenticated user
_protected = [Depends(require_user)]
app.include_router(inbox.router, prefix=settings.api_prefix, dependencies=_protected)
app.include_router(timesheets.router, prefix=settings.api_prefix, dependencies=_protected)
app.include_router(employees.router, prefix=settings.api_prefix, dependencies=_protected)
app.include_router(employee_matcher.router, prefix=settings.api_prefix, dependencies=_protected)
app.include_router(upload.router, prefix=settings.api_prefix, dependencies=_protected)
app.include_router(files.router, prefix=settings.api_prefix, dependencies=_protected)
app.include_router(pipeline.router, prefix=settings.api_prefix, dependencies=_protected)


@app.get("/health")
async def health():
    return {"status": "ok", "environment": settings.environment,
            "auth_enabled": settings.auth_enabled,
            "email_provider": settings.email_provider,
            "extraction_engine": settings.extraction_engine}
