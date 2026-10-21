# Timesheets Automation — Timesheet Intelligence Portal

Email-driven timesheet leave extraction with manager approval.

> **System architecture (auth, RBAC, OTP/CAPTCHA, Redis, Celery, Docker, tests):**
> see **[docs/SYSTEM.md](docs/SYSTEM.md)**.  
> **Extract / Accept / vault:** **[docs/EXTRACTION_FLOWS.md](docs/EXTRACTION_FLOWS.md)** ·
> **[docs/EXTRACTION_TWO_PASS.md](docs/EXTRACTION_TWO_PASS.md)**.  
> **Bulk roster upload (many employees, one sheet):** **[docs/BULK_ROSTER.md](docs/BULK_ROSTER.md)**.  
> **Roles, login hardening, data protection:** **[docs/SECURITY.md](docs/SECURITY.md)**.  
> Run tests with `bash scripts/test.sh`. Default admin: `admin` / `admin` — change it before going live.

## Product flow (current)

1. **Inbox** — sync mail, preview attachments, **Extract Email** (two-pass vision
   over the thread). Each message shows one status badge: **New** (dark blue — no
   extract has read it yet, including a fresh reply in an already-extracted
   thread), **Extracted** (yellow — read, items awaiting review) or **Ingested**
   (green — filed; replaces Extracted). Thread summary expands by default.
2. **Pipeline / Compare & Fix** — one review row per **employee + month**. AI may
   *recommend* Accept; nothing is filed until a human presses Accept.
3. **Accept** — writes `timesheet_records` + File Vault under  
   `Manager / Name (ACO-…, DCO-…) / Month-Year /`.  
   - Multi-employee email → only that person’s attachment(s).  
   - Same filename again → dated copy (never overwrite).
4. **Upload / Manual Entry / Save .eml to Vault** — same extract core or direct filing.
5. **Bulk upload** — one roster sheet listing many employees → one review item per
   employee, same Accept step. The reader is picked automatically and the preview
   shows the detected format and whether AI was used.

## What's included
- **Email Inbox** — read, Extract Email, Auto Extract (Start/Stop is **admin-only**),
  Save .eml to vault, archive.
- **Upload** — PDF/DOCX/XLSX/images/`.eml`; same two-pass extract → review queue.
- **Bulk upload** — monthly rosters: wide / long / HHRC spreadsheets and HHRC PDFs are
  read from their cells with **no AI**; FAZAA attendance reports and scanned rosters
  use the vision model. See [docs/BULK_ROSTER.md](docs/BULK_ROSTER.md).
- **Files** — File Vault browse / upload / ZIP download (ACO/DCO folder names).
- **Employee Matcher** — create / edit / deactivate + Excel import; ACO and DCO numbers.
  An inactive employee stays in the Dashboard and Export for the month they were
  inactivated in (and earlier months) and drops off from the next.
- **Dashboard / Records** — month-wise roll-ups, leave buckets, approval sign-off.
- **System notice** — admin popup for all users with a single OK; the admin sees who
  clicked OK (see [docs/SYSTEM.md](docs/SYSTEM.md#system-notice)).
- **Pipeline** — staged extracts, Compare & Fix, Retry from raw copy.

## Roles
| Role | Access |
|------|--------|
| `admin` | Everything, incl. user admin, Auto Extract Start/Stop, extraction debug |
| `user` | All business pages, read + write + delete |
| `viewer` | Read-only everywhere (can download); no writes |
| `vault_matcher` | Employee Matcher + File Vault only; **cannot delete**; every other page is blocked at the API |

Enforced in the API, not just the UI — matrix and details in
[docs/SECURITY.md](docs/SECURITY.md).

## Swappable providers (mock → real, all config-only)
| Concern | Config | Now | Later |
|---|---|---|---|
| Email | `EMAIL_PROVIDER` | `mock` | `graph` (Microsoft Graph) |
| Extraction | `EXTRACTION_ENGINE` | `mock` | `vision` (OpenAI-compatible) |
| File store | `STORAGE_PROVIDER` | `local` | `s3` · `onedrive` |
| Database | `DATABASE_URL` | PostgreSQL (local/Docker) | AWS RDS |

**Deleting mock entirely:** set the three providers to their real values, then remove
`app/seed/mock_data.py`, `app/services/email_provider/mock_provider.py`, and
`app/services/extraction/mock_engine.py`. Startup seeding no-ops if the mock data is gone,
and the factories only import a mock module when its provider is selected — so nothing else breaks.

---

## Quick start

> **Easiest path is Docker** (`bash scripts/dev/start.sh`) — it brings up
> PostgreSQL + Redis + the Celery worker + frontend. The manual steps below need
> a reachable PostgreSQL (set `DATABASE_URL`, or `docker compose -f
> docker-compose.dev.yml up -d db redis`).

### 1. Backend (terminal 1)
```bash
cd backend
python -m venv .venv && source .venv/bin/activate     # optional
pip install -r requirements.txt
# point at your Postgres (Docker db service, local, or AWS RDS):
export DATABASE_URL=postgresql+asyncpg://timesheet:timesheet@localhost:5432/timesheet
uvicorn app.main:app --reload --port 8000
```
- API: http://localhost:8000
- Interactive docs: http://localhost:8000/docs
- Sign in with **admin / admin** (configurable in `.env`).

### 2. Frontend (terminal 2)
```bash
cd frontend
npm install
npm run dev
```
- App: http://localhost:5173  (Vite proxies `/api/*` to the backend automatically)

On first boot the backend creates its PostgreSQL tables, seeds the mock employee matcher list
(`all_employee_data`), and the inbox shows 6 mock emails.

### Try the flow
1. **Inbox** → open a thread → **Extract Email**.
2. **Pipeline** → open Compare & Fix → review leave → **Accept & file record**.
3. **Files** → confirm vault folder uses ACO/DCO when set on the employee.
4. **Dashboard / Record** → green/yellow roll-up; approve sign-off if needed.

---

## Architecture

Full architecture: **[docs/SYSTEM.md](docs/SYSTEM.md)**. Security: **[docs/SECURITY.md](docs/SECURITY.md)**.
Extract / Accept / vault: **[docs/EXTRACTION_FLOWS.md](docs/EXTRACTION_FLOWS.md)**.

```
backend/app/
  core/      config · database · cache · celery_app · security · crypto · pii
  models/    auth_users · all_employee_data (aco_number, dco_number) · email · timesheet · pipeline
  api/       deps (RBAC) · routes/ (auth, admin, inbox, pipeline, employees, employee_matcher,
             upload, bulk_upload, files, portal_*, …)
  services/  auth/ · employee/ · extract_email/ · bulk_roster/ · extraction/ · llm/ ·
             pipeline/ · storage_provider/{local,s3,onedrive} · tasks (Celery)
  seed/  alembic/ (migrations through 0022+)  tests/
frontend/src/  api/client.ts · components/ · pages/ (Dashboard, Inbox, Upload,
               Pipeline, Employees, Files, Record, Login, admin/…)
```

### Extract → review → file

1. **Extract Email / Upload** — two-pass vision (`extract_email/`) packs the
   thread, classifies (Pass 1), extracts leave (Pass 2), groups by
   **employee + month**, stages `PipelineFile` rows (`NEEDS_REVIEW`).
2. **Compare & Fix Accept** — no LLM; `ingest_manual_entry` unions
   `source_files`, validates, files under ACO/DCO folder (deduped names;
   multi-employee isolates attachments), upserts `TimesheetRecord`, marks
   email **ingested**.
3. **Retry** re-reads pipeline-raw; **Archive** never extracts.

---

## Going to production

Everything below is config-only; **no caller code changes**.

### Swap mock email → Microsoft Graph
1. Register an app in Entra ID; note Tenant ID + Client ID.
2. Add **application** permission `Mail.Read` and grant admin consent.
3. Create a client secret (or certificate).
4. **Lock it to one mailbox** with an Exchange Application Access Policy
   (otherwise `Mail.Read` can read every mailbox in the tenant).
5. Implement `app/services/email_provider/graph_provider.py` (outline is in the file).
6. Set in `.env`:
   ```
   EMAIL_PROVIDER=graph
   GRAPH_TENANT_ID=...
   GRAPH_CLIENT_ID=...
   GRAPH_CLIENT_SECRET=...
   GRAPH_MAILBOX=timesheets@yourcompany.com
   GRAPH_FOLDER=Inbox
   ```
   (uncomment `msal` + `httpx` in requirements.txt)

### Activate vision extract
Two-pass prompts live in `app/services/extract_email/` (`triage_prompt.py`,
`thread_prompt.py`); rendering in `extraction/file_processor.py`. Turn on:
```
EXTRACTION_ENGINE=vision
OPENAI_API_KEY=sk-...
OPENAI_VISION_MODEL=gpt-4o
```
Two model settings, independent of each other:

| Setting | Drives |
|---------|--------|
| `OPENAI_VISION_MODEL` | **All vision extraction** — Extract Email passes 1 & 2, previews, bulk-roster vision reads |
| `AGENT_CHAT_MODEL` | Ask-AI chat (and the FAZAA attendance-report reader) |

Use any OpenAI-compatible gateway by setting `LLM_PROVIDER` and `OPENAI_BASE_URL`
(e.g. OpenRouter). After editing `.env`, **recreate** the containers
(`docker compose … up -d`) — `restart` does not re-read it.
Details: [`docs/EXTRACTION_TWO_PASS.md`](docs/EXTRACTION_TWO_PASS.md),
[`docs/SYSTEM.md`](docs/SYSTEM.md#ai-models--two-independent-settings).

### Use your Postgres
```
docker compose -f docker-compose.postgres.yml up -d
# backend/.env:
DATABASE_URL=postgresql+asyncpg://timesheet:timesheet@localhost:5432/timesheet_db
# uncomment asyncpg in requirements.txt
```
Schema migrations: Alembic (`alembic upgrade head` — current head is
`0033_notice_ack`; migrations also run automatically when the backend starts).
See [`docs/DATABASE_MIGRATIONS.md`](docs/DATABASE_MIGRATIONS.md).

### Other production notes
- Auth is built in (JWT + OTP / TOTP / CAPTCHA / RBAC, with rate limiting on both the
  internal and the employee-portal login) — configure before exposing publicly.
- For large mailboxes, Graph **delta** sync + Celery; Auto Extract skips already-done threads.
- Storage swaps to S3 via `STORAGE_PROVIDER=s3` — see [`docs/DATA_STORAGE.md`](docs/DATA_STORAGE.md).

### Deploying a new version (Docker, prod)
`scripts/prod/start.sh` is just `docker compose … up --build -d`. It does **not**
`git pull` and does **not** restart nginx, so:
```
git pull origin <branch>
bash scripts/prod/start.sh          # rebuilds backend + frontend images, recreates changed containers
docker compose -f docker-compose.prod.yml restart nginx   # re-resolve the recreated containers' IPs
```
The worker reports `healthy` only while Celery answers a ping (prod compose
overrides the image's HTTP healthcheck). More commands: `commands/prod.txt`,
[`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md).

---

## Data model highlights

`all_employee_data`: employee_id, name, dco_number, account_manager, employee_email_id.

`timesheet_records`: extracted + matched identity, canonical leave buckets
(annual / remote / sick / unpaid / absent / public_holiday), `validation_status`
(verified | manual_review), `llm_summary` + `hr_flags`, `approval_detected`
(from screenshot) + `approval_status` (your sign-off), and `storage_folder`.
