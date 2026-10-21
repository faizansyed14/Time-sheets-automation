# System Architecture

Scalable, service-oriented layout with caching, a task queue, secure auth, an
admin-configurable AI layer, Docker for dev + prod, and an end-to-end test
suite.

```
┌──────────┐     ┌─────────────────────────── backend (FastAPI) ───────────────────────────┐
│ frontend │ ──► │ api/routes  auth · admin · inbox · pipeline · employees · upload · files  │
│  (React) │     │ api/deps    RBAC (require_user / write / full_access / admin)             │
│  nginx   │     │ services    auth/ · config/ · employee/ · extract_email/ · extraction/   │
└──────────┘     │             llm/ · pipeline/ · storage_provider/ · tasks (Celery)         │
                 │ core        config · database · cache · celery_app · security · crypto    │
                 │ models      auth_users · timesheet_records · pipeline_files · employees │
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
    deps.py                 RBAC dependencies (require_user / require_write / require_full_access / require_admin)
    routes/                 auth, admin, inbox, pipeline, employees, employee_matcher, upload,
                            bulk_upload, files, timesheets, portal_auth, portal_employee, …
  core/                     config, database, cache, celery_app, security, crypto, pii
  models/                   auth_users, timesheet_records, pipeline_files, email_message, employee, …
  schemas/                  pydantic request/response models
  seed/                     default admin + demo data
  services/
    auth/                   passwords, otp, captcha, rate_limit, email_otp
    config/                 runtime config overlay
    employee/               Excel matcher import
    extract_email/          ★ thread two-pass extract → group → stage (primary path)
      thread_collect.py     pack whole thread (bodies, files, images, nested .eml)
      triage_prompt.py      PASS 1 prompt (classify / approve / summarise)
      thread_prompt.py      PASS 2 prompt (transcribe leave dates)
      thread_extract.py     run pass 1 + pass 2, normalise JSON
      grouping.py           match employee + month, union leave buckets
      auto_accept.py        AI recommend-accept (never auto-files)
      staging.py            PipelineFile NEEDS_REVIEW rows (+ group_count)
      auto_extract.py       background extract; skip if nothing newer
      email.py / upload.py  Inbox + Upload entry points
      sheet_cache.py        Extracted/New filename record + last_extraction_at
    bulk_roster/            ★ one sheet, many employees (xlsx/PDF cells first, vision fallback)
                            — see BULK_ROSTER.md
    extraction/             vision_client, parser, file rendering, mock engine
    llm/                    LangChain provider factory
    pipeline/               ingestion (Accept / isolate / ACO folders) + matching
    storage_provider/       local · s3 · onedrive + archive; ACO/DCO labels; dedupe
    tasks.py                Celery task registry
backend/alembic/            schema migrations
backend/tests/              pytest suite (auth, extract, pipeline, matching, …)
frontend/src/
  pages/                    Dashboard, Inbox, Upload, Pipeline, Employees, Files, Export, …
  components/               PipelineCompareFixModal, ExtractionActivity, ThreadSummaryBox, …
```

## Frontend surfaces

| Route | Role |
|-------|------|
| `/inbox` | Sync mail, Extract Email, Auto Extract (Start/Stop widget is **admin-only**), thread summary (expanded by default) |
| `/upload` | Drag-drop files / .eml → same two-pass extract. **Bulk upload** tab: one roster sheet listing many employees → [`BULK_ROSTER.md`](BULK_ROSTER.md) |
| `/pipeline` | Review queue — **AI recommends** vs **Held**; Compare & Fix |
| `/files` | File Vault (`Manager/<Employee ACO/DCO>/<Month-Year>/`) |
| `/employees` | Matcher list (includes ACO / DCO numbers) |
| `/chat` | Agentic text chat (separate from vision extract) |

---

## Extract Email → Compare & Fix → filed record

You click **Extract Email** on an inbox message (or upload an `.eml`/sheet). The
model **recommends** accept when checks pass; **nothing is saved** until a human
presses **Accept & file record** in Compare & Fix.

### Architecture (thread two-pass)

When `EXTRACTION_ENGINE=vision` and a real OpenAI key is set, Extract Email /
Upload use **one conversation, two vision calls**:

```
┌──────────────────────── thread payload ────────────────────────┐
│  message bodies (PII-scrubbed)                                 │
│  every attachment (PDF/Excel/…) + inline images                │
│  nested emails opened and labelled                             │
└───────────────────────────────┬────────────────────────────────┘
                                │
              ┌─────────────────▼─────────────────┐
              │  PASS 1 — TRIAGE (vision model)   │
              │  Classify items, employee,        │
              │  format_id, approval, summary.    │
              │  Do NOT list leave dates.         │
              │  HR Leave History → leave_cert.   │
              │  Logos/banners → noise.           │
              └─────────────────┬─────────────────┘
                                │ only timesheet + leave_certificate
              ┌─────────────────▼─────────────────┐
              │  PASS 2 — EXTRACT (vision model)  │
              │  Read confirmed sheets only.      │
              │  Transcribe leave buckets +       │
              │  coverage (days_covered, …).      │
              └─────────────────┬─────────────────┘
                                │
              ┌─────────────────▼─────────────────┐
              │  SERVER (no AI)                   │
              │  name-first match → group by      │
              │  employee+month → union buckets   │
              │  → auto_accept.evaluate()         │
              │  → stage NEEDS_REVIEW             │
              └─────────────────┬─────────────────┘
                                │
              ┌─────────────────▼─────────────────┐
              │  HUMAN — Compare & Fix            │
              │  Accept → timesheet_records + vault│
              └───────────────────────────────────┘
```

Code: `services/extract_email/thread_extract.py`, prompts in `triage_prompt.py` /
`thread_prompt.py`. Fallback when vision is off: per-sheet pipeline
(`build_pipeline()`).

### What happens, step by step

```
 1. Click "Extract Email"     →  whole thread packed (subject, bodies,
    (browser)                    attachments, nested emails)

 2. Collect on server         →  ThreadPayload: files, images, body text,
                                 digests, format hints. PII scrubbed.

 3. PASS 1 — triage           →  vision call(s) over the thread (batched by
                                 item count when needed). Output: items[],
                                 approval, summary, noise[].

 4. PASS 2 — extract          →  vision call(s) over ONLY confirmed sheets
                                 (batched by image count). Leave dates +
                                 coverage fields.

 5. Match & group             →  name-first employee match; one PipelineFile
    (server, no AI)              per employee+month; union leave; store
                                 group_count on meta.

 6. AI recommend (optional)   →  auto_accept.evaluate — recommendation ONLY.
                                 Always staged as NEEDS_REVIEW. Never auto-files.

 7. Compare & Fix             →  LEFT: editable leave buckets.
                                 RIGHT: original email / attachments.
                                 Badge: "AI recommends" (green) or "Held" (amber).

 8. Accept → filed            →  DB record + vault under
                                 <Manager>/<Name (ACO-…, DCO-…)>/<Month-Year>/.
                                 Multi-employee runs file that person's
                                 attachment(s) only; single-employee keeps
                                 the whole .eml. Existing filenames get a
                                 dated suffix (never overwrite).
                                 Inbox message marked ingested.
```

### AI recommend vs Held

Recommendation only when **all** checks pass (`auto_accept.py`):

1. Employee matched in matcher (not a guess).
2. Month + year present.
3. Validation clean (no overlap / duplicate full-month flags).
4. Full-month coverage from pass-2 day accounting (no unaccounted / uncertain days that block).
5. Any `leave_certificate` sheet has **extracted dates** (empty cert → Held).

There is **no** per-client-template gate (no `generic` / format registry on this path).

Otherwise staged with **blockers** listed for the reviewer. Record is still
never written until Accept.

### Leave evidence (HR app screenshots)

Attendance PDFs (e.g. Digital Dubai) often show ABSENCE/PERMISSION only —
**approved annual leave may live only in a Leave History screenshot** in the
same email. Pass 1 must classify those as `leave_certificate` (not noise);
pass 2 expands Approved date ranges into leave buckets; grouping unions them
with the timesheet.

### PII — BEFORE the AI, always

- **Text** — `core/pii.py` → `scrub_text` inside `vision_client`.
- **Images** — body/subject scrubbed before render (`file_processor`).
- Names, employee IDs, dates, hours are **not** redacted.
- Raw `.eml` and employee DB are **never** sent to the model.

### How many AI calls?

| Path | Calls |
|------|-------|
| Thread two-pass (default) | **≥ 2** — pass 1 (+ batches) + pass 2 (+ batches) |
| Auto Extract skip | **0** — already extracted, nothing newer |
| Vision unavailable | 0 — local/mock engine / hard fail depending on path |

No separate summary or validation LLM. Summary comes from pass 1 JSON;
date checks are server-side.

### Inbox badges

A message shows **exactly one** of these (plus *Archived*) — never "New" and
"Extracted" together:

| Badge | Colour | Meaning |
|-------|--------|---------|
| Ingested | Green | Every staged item of the thread has been Accepted and filed, or the message itself was marked ingested on Accept. Wins over Extracted. |
| Extracted | Yellow | Extract Email has read this message (it had already arrived when the thread was last read) and something is still awaiting review. |
| New | Dark blue | No Extract Email run has read this message yet — including a **new reply in an already-extracted thread**, which goes back to New until it is extracted. |

"Read" is judged by the run's own `snapshot_at` (when the thread was actually
collected), not by the review row's `updated_at`, so Accepting or re-matching an
item can't make a reply that arrived afterwards look already-read. The
New / Extracted / Ingested **filters** use the same rules and judge a threaded
conversation by its newest message, so a filter and the badge on the row never
disagree. Details: [`EXTRACTION_FLOWS.md`](EXTRACTION_FLOWS.md) §6.

### Key guarantees

- AI sees scrubbed text + labelled attachments/images only.
- Matching / validation / recommend gate are server code.
- **Human Accept** is the only path that files a record (plus Manual Entry / Save-to-Vault).
- Re-extract on the same thread updates the same review items by tag.
- Vault never silently overwrites; multi-employee Accept isolates attachments.

Deep dive (APIs, vault, entry points): [`EXTRACTION_FLOWS.md`](EXTRACTION_FLOWS.md).
Source of truth for prompts: `triage_prompt.py`, `thread_prompt.py`.
See also [`EXTRACTION_TWO_PASS.md`](EXTRACTION_TWO_PASS.md).

---

## Prompts sent to the model

Two layers (no per-client template registry on the wired path):

| Layer | When | File | What |
|-------|------|------|------|
| **Pass 1 triage** | Always (≥1 call, batched) | `triage_prompt.py` | Classify items, approval, thread summary |
| **Pass 2 extract** | Always for confirmed sheets (≥1 call, batched) | `thread_prompt.py` | Leave buckets + day accounting + admin calendar |

Full Pass 1 / Pass 2 prompt text, calendar injection, and JSON shapes live in
[`EXTRACTION_TWO_PASS.md`](EXTRACTION_TWO_PASS.md) and the Python modules
`triage_prompt.py` / `thread_prompt.py`. There is **no** per-client
`format_prompts` registry on the wired Extract Email path.

---

## Caching & queue

- **Redis** backs the cache (OTP/CAPTCHA state, rate-limit sliding windows,
  config overlay) and the **Celery** broker/result backend.
- `core/cache.py` transparently **falls back to an in-memory store** if Redis is
  unreachable — so dev and the test suite need no external services.
- **Celery** (`core/celery_app.py`, `services/tasks.py`) runs OTP email delivery
  and optional async ingestion off the request path. `CELERY_TASK_ALWAYS_EAGER`
  runs tasks inline when there's no worker (dev/tests).

## Authentication (production-ready)

Two-step login with a per-user second factor and RBAC (`admin` / `user` /
`viewer` / `vault_matcher` — see [`SECURITY.md`](SECURITY.md) for the role matrix):

1. `POST /auth/login` — username + password (bcrypt). On success exactly **one**
   challenge follows, chosen per user by an admin (**every role, admins
   included**):
   - **OTP user** → a 6-digit code is emailed (Graph) and a short-lived
     *login token* is returned;
   - **TOTP user** → authenticator-app code (QR enrolment on first login);
   - **CAPTCHA user** → a word-CAPTCHA challenge + login token.

   The response for a still-pending login carries only the challenge and login
   token — **no user details** (name / email / role).
2. `POST /auth/verify-otp` / `verify-totp` / `verify-captcha` → access token
   (JWT) plus the user object.

Security controls: **sliding-window rate limiting** (login + every verify
endpoint), **OTP expiry / max attempts / resend limit + cooldown**, single-use
codes, **device-fingerprint binding** of the login flow, constant-time
comparisons and encrypted secrets at rest. CAPTCHA has a **refresh**
(`GET /auth/captcha`).

The employee portal has its own password-only login (`POST /portal/auth/login`),
separate token namespace, and its own rate-limit bucket (`rl:portal-login`,
429 when exceeded).

Default admin (`admin`/`admin`) is seeded from `.env` and configurable — change
it before exposing the app.

## Admin panel

- **Users & access** (`/admin/users`): create users, assign OTP emails, switch a
  user between **OTP** and **CAPTCHA**, enable/disable, set roles.
- **System notice** (card on the Users & access page): one sentence shown as a
  popup to every signed-in user, any role. See [System notice](#system-notice).
- **AI Settings** (`/admin/settings`): read-only view of active OpenAI models and
  key status. All tuning lives in `.env`; recreate the containers after changes.

## System notice

An admin writes one message and ticks **Show to everyone**; every signed-in user
(admin, user, viewer, vault_matcher) then gets a popup with a single **OK**
button — no close “X”, Escape and clicking outside do nothing.

- **OK is remembered on the server** against the user (`system_notice.acked_by`, a
  JSON `{user_id: time}` column on the one notice row — no extra table), so the
  popup never returns for them on any browser or device.
- **Admin sees who clicked.** Under the notice card, only while it is being shown to
  everyone, a collapsible **Acknowledged — N of M users clicked OK** list shows a
  👍 and the time next to each user who clicked, and “Not yet” for the rest (active
  users only; refreshes every 15 s).
- **A new announcement resets everyone.** Changing the message, or switching the
  notice off and on again, clears `acked_by` so it shows to everyone again. Saving
  the same text with the same setting does not. A click on a message the admin has
  since changed is ignored.
- A click is not an admin edit: it does not change `updated_at` / `updated_by`.
- API: `GET /notice` (all roles; includes `acknowledged` for the caller),
  `POST /notice/ack` (all roles), `PUT /notice` and `GET /notice/acks` (admin).

## Inactive employees are month-wise

Marking an employee **inactive** keeps their row, records and vault files. The
month it happens in is recorded (`all_employee_data.inactivated_at` plus
`inactive_ym`, year×100+month in UAE time, e.g. `202610`), and the **Dashboard**
and the **Export** (preview and XLSX) then use the same rule per month `M`:

| Employee is shown for month `M` when… |
|---|
| they are **active**, or |
| they were inactivated **in `M` or later** (so: that month and every earlier month), or |
| the pipeline has **data for them in `M`** — a filed record or a received sheet |

So someone inactivated in October still appears for October and earlier months, and
is gone from November on (unless a record/sheet exists for them in a later month,
which is never hidden). Headline counts and the “missing” list follow the same
rule. Reactivating clears the month. Editing an inactive employee does not move it,
and re-marking one inactive keeps the original month.

Employees that were already inactive **before** this was introduced have no recorded
month (`inactive_ym` is empty), so they only appear in months where they have data.
Rule code: `services/employee/activity.py` (SQL form for the Dashboard, Python form
for the Export). The Employees page shows “Inactive · Oct 2026” on the badge.

## AI models — two independent settings

| Setting | Used by | Code |
|---------|---------|------|
| `OPENAI_VISION_MODEL` | **All vision extraction** — Extract Email Pass 1 / Pass 2, previews, bulk-roster vision reads | `services/extraction/vision_client.py` (`model_for`, `chat_call`) |
| `AGENT_CHAT_MODEL` | **Ask-AI chat** (`/chat`), plus the FAZAA attendance-report reader | `services/llm/provider.py` (LangChain) |

`EXTRACTION_MODEL` is only read by the LangChain factory (`provider.py`, the
"extraction" kind and the Admin → AI Settings display); vision calls do not use it.
Keep it equal to `OPENAI_VISION_MODEL` so the settings page shows the model that
is really in use.

The two main settings can be different models (e.g. a vision-capable model for
extraction and the cheapest model for chat). Provider/base URL come from
`LLM_PROVIDER` / `OPENAI_BASE_URL`; with an OpenRouter-style gateway, pages are
rendered to images client-side first.

`vision_client.chat_call` notes:
- No OpenRouter provider preference is sent (a `sort: "price"` pin used to route
  to the cheapest, slowest host — see [`BULK_ROSTER.md`](BULK_ROSTER.md)). The
  LangChain chat factory (`provider.py`, used for Ask-AI chat) still pins
  `sort: "price"` for OpenRouter.
- `_MAX_TOKENS = 8000` per call (reasoning tokens count), 3 attempts, per-attempt
  timeout `OPENAI_TIMEOUT` (default 120 s).
- A reasoning model that truncates its JSON reply makes the call fail over to the
  retry / fallback path rather than returning partial data.

**Changing a model:** edit `.env`, then **recreate** the containers
(`docker compose … up -d`). `restart` is not enough — compose reads `env_file` only
when a container is created.

`services/llm/provider.py` builds a LangChain OpenAI chat model from `.env`
settings, so model/key changes are an env change, not a code change.
Uses a `ChatPromptTemplate | model | StrOutputParser` chain and an LRU on
construction. Vision extract uses `services/extraction/vision_client.py`
directly (not this chat chain).

## Docker, environments, scripts

**Dev mirrors prod** — identical stack (Postgres + Redis + backend + Celery
worker + auth + real task queue); dev only **reduces resources** (1 reload
backend worker vs 4, Celery concurrency 1 vs 4, smaller CPU/memory limits) and
adds hot-reload + volume mounts.

| | dev (`docker-compose.dev.yml`) | prod (`docker-compose.prod.yml`) |
|---|---|---|
| DB | Postgres | Postgres |
| Cache/queue | Redis + Celery worker | Redis + Celery worker |
| Backend | `uvicorn --reload` (1) | `uvicorn --workers 4` |
| Worker | `--concurrency 1` | `--concurrency 4` |
| Frontend | Vite (hot reload) | nginx (built SPA) |
| Limits | small | large |

Prod worker health: the worker image inherits the backend's HTTP healthcheck,
which can never pass for a Celery process, so `docker-compose.prod.yml` overrides
it with `celery … inspect ping` against the worker's own node. `docker ps` now
shows the worker `healthy` only when Celery is actually alive (cosmetic — nothing
restarts or waits on it).

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

**Database is PostgreSQL only**. The whole app talks to it through SQLAlchemy +
asyncpg, so moving between local Docker Postgres and a managed instance is a
one-line change:

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

Pytest via httpx against the ASGI app (Celery eager + in-memory cache where
configured). Coverage includes auth, captcha/admin, extract email / thread
extract, auto-accept recommendation, matching, format prompts, pipeline, S3.

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
Related docs: [`ENVIRONMENTS.md`](ENVIRONMENTS.md), [`EXTRACTION_FLOWS.md`](EXTRACTION_FLOWS.md),
[`SECURITY.md`](SECURITY.md), [`DATA_STORAGE.md`](DATA_STORAGE.md).
