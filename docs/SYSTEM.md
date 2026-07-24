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
  services/
    auth/                   passwords, otp, captcha, rate_limit, email_otp
    config/                 runtime config overlay (admin-editable AI settings)
    employee/               Excel matcher import
    extraction/             mock engine, vision client, parser, validation, file rendering
    llm/                    LangChain provider factory
    agents/                 full_email_extract + agentic chat
    pipeline/               ingestion + employee matching
    storage_provider/       local · s3 · onedrive + archive (zip)
    tasks.py                Celery task registry
backend/alembic/            schema migrations
backend/tests/              end-to-end pytest suite (auth, admin, infra, pipeline, s3)
```

## Extract Email → Compare & Fix → filed record

You click **Extract Email** on an inbox message, review the extracted leaves
next to the original, and press **Accept** to file the timesheet. Nothing is
saved until you accept.

### What happens, step by step

```
 1. Click "Extract Email"        →  the whole email is packed into one .eml
    (in the browser)                (subject, body, every attachment, and any
                                     forwarded emails inside it)

 2. The .eml is opened up         →  each attachment becomes ONE stitched JPEG
    ON THE SERVER                    + its text; the email body becomes an image
    (nothing sent anywhere yet)      too. These are the "sheets".

 3. PII is removed                →  email addresses and phone numbers are
    (still on the server)            stripped from the text AND from the images
                                     BEFORE anything leaves the server.

 4. Sheets sent to the AI         →  the ONLY step that talks to the AI. It reads
    (vision model)                   each sheet and returns: who it is, the
                                     month, the leave dates, and whether a
                                     manager approved.

 5. Results matched & grouped     →  names/IDs are matched to your employee list,
    ON THE SERVER (no AI)            grouped per employee + month, dates checked,
                                     a plain-English summary written.

 6. Staged for review             →  one review item per employee/month appears
                                     in the pipeline, pre-filled, with the whole
                                     email attached as evidence.

 7. Compare & Fix                 →  LEFT: the extracted leaves (you can edit).
    (in the browser)                 RIGHT: the original email + attachments.
                                     Fix anything, then Accept.

 8. Accept → filed               →  the record is saved to the database and the
                                     files land in the vault folder
                                     <Manager>/<Employee>/<Month-Year>/. The
                                     inbox message is marked "ingested".
```

### When and how is PII removed? — BEFORE the AI, always

PII (email addresses, phone numbers) is removed **on your server, before step 4**,
in two places so nothing slips through:

- **In the text** — the prompt is scrubbed at the moment of sending
  (`core/pii.py` → `scrub_text`, called inside `services/extraction/vision_client.py`).
- **In the images** — the email body/subject are scrubbed *before* they are
  rendered to a picture, so an address can never appear as pixels the AI could
  read (`services/extraction/file_processor.py`).

Emails become a stable placeholder (`person-3f2a1b@redacted.invalid`) and phones
become `[phone-redacted]`. **Names, employee IDs, dates and hours are never
touched** — those are the data being extracted, so accuracy is unaffected. The
`.eml` file itself and your employee database are **never** sent to the AI.

### How many AI (LLM) calls does one Extract Email make?

Only the vision reads in step 4 — **nothing else calls the AI**:

- **calls ≈ number of sheets ÷ 2** (sheets are sent 2 per call).
- A typical email (one timesheet + the body) = **1–2 calls**.
- There is **no separate "summary" AI call** and **no separate "validation" AI
  call** in this flow — the summary and the date-validation are done by plain
  server code (`services/extraction/validation.py`), not the model.
- If the AI is unavailable, each sheet falls back to a local, no-AI reader so
  the button still works.

(Extract Email and Upload share the **same** vision extraction pipeline — those
two, plus Manual Entry, are the only ways a sheet enters the system. Agentic
chat is a separate text model with tools and accepts no file uploads. Neither
uses a second validation LLM — summaries/flags stay in `validation.py`.)

### Key guarantees

- The AI only ever sees **scrubbed text and rendered images** — never the raw
  `.eml`, never your employee list.
- **Matching and validation happen after the AI, in plain server code** — the
  model proposes, your code checks, and a **human must Accept** before any
  record is written.
- Re-running Extract Email on the same message **replaces** the previous review
  items (no duplicates).

Full detail: `services/agents/full_email_extract.py` (top-of-file docstring).
Deep dive (all entry points, full prompts, APIs, vault): [`EXTRACTION_FLOWS.md`](EXTRACTION_FLOWS.md).

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
- **AI Settings** (`/admin/settings`): read-only view of active OpenAI models and
  key status. All tuning lives in `.env`; restart backend after changes.

## LangChain (provider-agnostic AI)
`services/llm/provider.py` builds a LangChain OpenAI chat model from `.env`
settings, so model/key changes are an env + restart change, not a code change. Uses a
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
- `.env.example` — **only** committed env template (active keys + commented
  LOCAL / DEV / PROD profile blocks). Copy to root `.env` and apply one profile.
- `scripts/dev/{start,stop}.sh`, `scripts/prod/{start,stop}.sh`, `scripts/test.sh`.
- **`commands/dev.txt`** and **`commands/prod.txt`** — copy-paste Docker commands
  (up/down/logs/exec/psql/backup/scale) for each environment.

### About `backend/.env`
Not required and not used by Docker — compose injects config from root `.env`
(gitignored; start from `.env.example`). The image `.dockerignore`s `.env*`.
Keep a `backend/.env` **only** for a no-Docker local run; otherwise delete it.

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
| `onedrive` | OneDrive / SharePoint (**stub — not implemented; app will fail at startup**) |

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
# Docker (cp .env.example → .env, apply LOCAL/DEV/PROD profile first)
bash scripts/dev/start.sh        # admin / admin   (frontend :5173, api :8000)

# Local backend (needs a reachable Postgres; set DATABASE_URL in backend/.env)
cd backend && python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --reload --port 8000
cd ../frontend && npm install && npm run dev

# Docker prod (after editing .env secrets)
bash scripts/prod/start.sh
```

Docker command references: `commands/dev.txt`, `commands/prod.txt`.
