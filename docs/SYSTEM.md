# System Architecture

Scalable, service-oriented layout with caching, a task queue, secure auth, an
admin-configurable AI layer, Docker for dev + prod, and an end-to-end test
suite.

```
┌──────────┐     ┌─────────────────────────── backend (FastAPI) ───────────────────────────┐
│ frontend │ ──► │ api/routes  auth · admin · inbox · pipeline · employees · upload · files  │
│  (React) │     │ api/deps    RBAC (require_user / require_write / require_admin)           │
│  nginx   │     │ services    auth/ · config/ · employee/ · extraction/ · llm/ · pipeline/  │
└──────────┘     │             storage_provider/{local,s3,onedrive} · tasks (Celery)         │
                 │ core        config · database · cache · celery_app · security · crypto    │
                 │ models      auth_users · app_config · timesheet_records · …               │
                 └───────┬─────────────────┬──────────────────┬──────────────────────────────┘
                         │                 │                  │
                    ┌────▼────┐      ┌──────▼──────┐    ┌──────▼───────────┐
                    │  Redis  │      │ Celery work │    │   PostgreSQL     │
                    │ cache + │      │  (OTP mail, │    │ (local / Docker  │
                    │ broker  │      │  ingestion) │    │  / AWS RDS)      │
                    └─────────┘      └─────────────┘    └──────────────────┘
```

## Project structure
```
backend/app/
  api/
    deps.py                 RBAC dependencies (require_user / require_admin)
    routes/                 auth, admin, inbox, pipeline, employees, upload, files, timesheets
  core/                     config, database, cache, celery_app, security, crypto
  models/                   auth_users, app_config, timesheet_records, pipeline_files, …
  schemas/                  pydantic request/response models
  seed/                     default admin + demo data
  migrations/               idempotent startup upgrade (Alembic for real prod)
  services/
    auth/                   passwords, otp, captcha, rate_limit, email_otp
    config/                 runtime config overlay (admin-editable AI settings)
    employee/               Excel matcher import
    extraction/             mock + vision engines, parser, validation, file rendering
    llm/                    LangChain provider factory
    pipeline/               ingestion + employee matching   (the extraction pipeline)
    storage_provider/       local · s3 · onedrive + archive (zip)
    tasks.py                Celery task registry
backend/tests/              end-to-end pytest suite (auth, admin, infra, pipeline, s3)
```

## Caching & queue
- **Redis** backs the cache (OTP/CAPTCHA state, rate-limit sliding windows,
  config overlay) and the **Celery** broker/result backend.
- `core/cache.py` transparently **falls back to an in-memory store** if Redis is
  unreachable — so dev and the test suite need no external services.
- **Celery** (`core/celery_app.py`, `services/tasks.py`) runs OTP email delivery
  and optional async ingestion off the request path. `CELERY_TASK_ALWAYS_EAGER`
  runs tasks inline when there's no worker (dev/tests).

## Authentication (production-ready)
Two-step login with per-user second factor and RBAC (`admin` / `user`):

1. `POST /auth/login` — username + password (bcrypt). On success:
   - **admin** → bypasses 2FA, gets an access token immediately;
   - **OTP user** → a 6-digit code is emailed (Graph) and a short-lived
     *login token* is returned;
   - **CAPTCHA user** → a word-CAPTCHA challenge + login token.
2. `POST /auth/verify-otp` / `verify-captcha` → access token (JWT).

Security controls: **sliding-window rate limiting** (login + OTP verify),
**OTP expiry / max attempts / resend limit + cooldown**, single-use codes,
**device-fingerprint binding** of the login flow, constant-time comparisons,
encrypted secrets at rest, and the admin OTP bypass. CAPTCHA has a **refresh**
(`GET /auth/captcha`).

Default admin (`admin`/`admin`) is seeded from `.env` and configurable.

## Admin panel
- **Users & access** (`/admin/users`): create users, assign OTP emails, switch a
  user between **OTP** and **CAPTCHA**, enable/disable, set roles.
- **AI Settings** (`/admin/settings`): edit prompts, choose provider
  (OpenAI / DeepSeek / …) + API keys, and the controls `EXTRACTION_ENGINE`,
  `VISION_IMAGE_DETAIL`, `VALIDATION_MODEL`, `ENABLE_TEXT_VALIDATION`. **Update &
  Test** from the UI; changes apply **live** (overlaid on `.env`, no redeploy).
- Stored in a dedicated, isolated **`app_config`** table; secret values
  (API keys) are **encrypted at rest** (`core/crypto.py`) and masked in the API.

## LangChain (provider-agnostic AI)
`services/llm/provider.py` builds a LangChain chat model from the active config
(`ChatOpenAI` with per-provider base_url/key covers OpenAI, DeepSeek, vLLM …),
so switching providers is a settings change, not a code change. Uses a
`ChatPromptTemplate | model | StrOutputParser` chain and an LRU on construction.

## Docker, environments, scripts
**Dev mirrors prod** — identical stack (Postgres + Redis + backend + Celery
worker + auth + real task queue); dev only **reduces resources** (1 reload
backend worker vs 4, Celery concurrency 1 vs 4, smaller CPU/memory limits) and
adds hot-reload + source mounts.

| | dev (`docker-compose.dev.yml`) | prod (`docker-compose.prod.yml`) |
|---|---|---|
| DB | Postgres | Postgres |
| Cache/queue | Redis + Celery worker | Redis + Celery worker |
| Backend | `uvicorn --reload` (1) | `uvicorn --workers 4` |
| Worker | `--concurrency 1` | `--concurrency 4` |
| Frontend | Vite (hot reload) | nginx (built SPA) |
| Limits | small | large |

- `backend/Dockerfile`, `frontend/Dockerfile` (dev + nginx prod targets).
- `.env.example` — local backend template; `.env.dev` / `.env.prod` — Docker
  examples (copy one to `.env` at repo root).
- `scripts/dev/{start,stop}.sh`, `scripts/prod/{start,stop}.sh`, `scripts/test.sh`.
- **`commands/dev.txt`** and **`commands/prod.txt`** — copy-paste Docker commands
  (up/down/logs/exec/psql/backup/scale) for each environment.

### About `backend/.env`
Not required and not used by Docker — compose injects config from root `.env`
(gitignored; copy from `.env.dev` or `.env.prod`), and the image `.dockerignore`s
`.env*`. Keep a `backend/.env`
**only** for a no-Docker local run (copy from `.env.example`); otherwise you can
safely delete it.

## Database & storage — portable via `.env`
**Database is PostgreSQL only** (SQLite was removed). The whole app talks to it
through SQLAlchemy + asyncpg, so moving between local Docker Postgres and a
managed instance is a one-line change:

```
# AWS RDS — no code changes:
DATABASE_URL=postgresql+asyncpg://USER:PASS@my-db.xxxx.rds.amazonaws.com:5432/timesheet
```

**File storage** goes through a `StorageProvider` interface, selected by
`STORAGE_PROVIDER`:

| value | backend |
|---|---|
| `local` (default) | local disk under `storage/` |
| `s3` | **AWS S3** (or any S3-compatible store via `S3_ENDPOINT_URL`) |
| `onedrive` | OneDrive / SharePoint (stub) |

Switch to S3 purely from `.env` — no code changes:
```
STORAGE_PROVIDER=s3
S3_BUCKET=my-timesheets
S3_PREFIX=timesheets
S3_REGION=us-east-1
AWS_ACCESS_KEY_ID=...        # omit on EC2/ECS to use the IAM role
AWS_SECRET_ACCESS_KEY=...
```
Listing, upload, preview, download-zip, rename and delete all work the same
against S3 (keys are mapped onto the Manager/Employee/Month folder model).

## Tests (`backend/tests/`)
26 end-to-end tests via httpx against the ASGI app (Celery eager + in-memory
cache, so only Postgres is needed): `test_auth_otp.py` (admin bypass, OTP
lifecycle, resend, fingerprint, rate limit, RBAC), `test_captcha_admin_config.py`
(CAPTCHA login, user mgmt, config get/set/test, secret masking, prompt
overrides), `test_infra.py` (cache, sliding window, Celery eager, LangChain
factory), `test_pipeline_e2e.py` (authed upload pipeline, pagination, coverage
search, content-endpoint query-token), `test_s3_storage.py` (S3 provider full
lifecycle via moto).

Tests run against a throwaway Postgres database (`TEST_DATABASE_URL`); tables
are dropped/recreated each run.
```
docker compose -f docker-compose.dev.yml --env-file .env up -d db   # a Postgres
createdb -h localhost -U timesheet timesheet_test                       # once
bash scripts/test.sh
```

## Quick start

```bash
# Docker dev / prod (copy .env.dev or .env.prod → .env first)
bash scripts/dev/start.sh        # admin / admin   (frontend :5173, api :8000)

# Local backend (needs a reachable Postgres; set DATABASE_URL in backend/.env)
cd backend && python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --reload --port 8000
cd ../frontend && npm install && npm run dev

# Docker prod (after editing .env secrets)
bash scripts/prod/start.sh
```

Docker command references: `commands/dev.txt`, `commands/prod.txt`.
