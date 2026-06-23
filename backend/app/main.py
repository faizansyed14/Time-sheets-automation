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

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api.deps import require_write
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


def _assert_prod_secrets() -> None:
    """Fail closed in production if the JWT secret is weak/default (OWASP A02)."""
    if not settings.is_prod:
        return
    weak = (not settings.jwt_secret
            or len(settings.jwt_secret) < 32
            or "change-me" in settings.jwt_secret.lower())
    if weak:
        raise RuntimeError(
            "Refusing to start in prod with a weak JWT_SECRET. Set a long random "
            "value, e.g. `python -c \"import secrets; print(secrets.token_urlsafe(48))\"`."
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    _assert_prod_secrets()
    # Schema management:
    #   - Docker / prod / AWS RDS: Alembic owns the schema. Set
    #     AUTO_CREATE_TABLES=false; `alembic upgrade head` runs before the app
    #     starts (see the compose `command` and scripts/db/migrate.sh).
    #   - Local quick-start / tests (AUTO_CREATE_TABLES=true, the default):
    #     create any missing tables from the models.
    if settings.auto_create_tables:
        await init_db()
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
    allow_origins=settings.cors_origins,         # explicit origins (never "*")
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Fingerprint"],
    expose_headers=["X-Captcha-Id"],
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """Baseline OWASP security headers on every API response."""
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    response.headers.setdefault(
        "Permissions-Policy", "geolocation=(), microphone=(), camera=()")
    if settings.is_prod:
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=63072000; includeSubDomains")
    return response


# public
app.include_router(auth.router, prefix=settings.api_prefix)
# admin (admin role enforced inside the router)
app.include_router(admin.router, prefix=settings.api_prefix)

# business routes — authenticated; viewers may read but not mutate (require_write
# allows safe methods for everyone and blocks writes for the read-only role).
_protected = [Depends(require_write)]
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
