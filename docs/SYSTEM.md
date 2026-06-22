# System Architecture

Scalable, service-oriented layout with caching, a task queue, secure auth, an
admin-configurable AI layer, Docker for dev + prod, and an end-to-end test
suite.

```
┌──────────┐     ┌─────────────────────────── backend (FastAPI) ───────────────────────────┐
│ frontend │ ──► │ api/routes  auth · admin · inbox · pipeline · employees · upload · files  │
│  (React) │     │ api/deps    RBAC dependencies (require_user / require_admin)              │
│  nginx   │     │ services    auth/{otp,captcha,rate_limit,email_otp} · llm/provider        │
└──────────┘     │             ingestion · extraction · config_service · tasks (Celery)      │
                 │ core        config · database · cache · celery_app · security · crypto    │
                 │ models      auth_users · app_config · timesheet_records · …               │
                 └───────┬─────────────────┬──────────────────┬──────────────────────────────┘
                         │                 │                  │
                    ┌────▼────┐      ┌──────▼──────┐    ┌──────▼───────┐
                    │  Redis  │      │ Celery work │    │  DB (SQLite  │
                    │ cache + │      │  (OTP mail, │    │  dev / Post- │
                    │ broker  │      │  ingestion) │    │  gres prod)  │
                    └─────────┘      └─────────────┘    └──────────────┘
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
**only** for a no-Docker local run (copy from `.env.example`, SQLite default);
otherwise you can safely delete it.

## Tests (`backend/tests/`)
End-to-end via httpx against the ASGI app, no external services required:
`test_auth_otp.py` (admin bypass, OTP lifecycle, resend, fingerprint, rate
limit, RBAC), `test_captcha_admin_config.py` (CAPTCHA login, user mgmt, config
get/set/test, secret masking, prompt overrides), `test_infra.py` (cache,
sliding window, Celery eager, LangChain factory), `test_pipeline_e2e.py`
(authed upload pipeline, pagination, coverage search).

```
bash scripts/test.sh           # or: cd backend && .venv/bin/python -m pytest
```

## Quick start

```bash
# Local (no Docker): SQLite + in-memory cache + eager Celery
cd backend && python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --reload --port 8000      # admin / admin
cd ../frontend && npm install && npm run dev

# Docker dev / prod (copy .env.dev or .env.prod → .env first)
bash scripts/dev/start.sh
bash scripts/prod/start.sh        # after editing .env secrets
```
