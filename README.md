# Timesheets Automation — Timesheet Intelligence Portal

Email-driven timesheet leave extraction with manager approval.

> **System architecture (auth, RBAC, OTP/CAPTCHA, Redis, Celery, Docker, tests):**
> see **[docs/SYSTEM.md](docs/SYSTEM.md)**.  
> **Extract / Accept / vault:** **[docs/EXTRACTION_FLOWS.md](docs/EXTRACTION_FLOWS.md)** ·
> **[docs/EXTRACTION_TWO_PASS.md](docs/EXTRACTION_TWO_PASS.md)**.  
> Run tests with `bash scripts/test.sh`. Default admin: `admin` / `admin`.

## Product flow (current)

1. **Inbox** — sync mail, preview attachments, **Extract Email** (two-pass vision
   over the thread). Status badges: **New** (dark blue), **Extracted** (yellow),
   **Ingested** (green). Thread summary expands by default.
2. **Pipeline / Compare & Fix** — one review row per **employee + month**. AI may
   *recommend* Accept; nothing is filed until a human presses Accept.
3. **Accept** — writes `timesheet_records` + File Vault under  
   `Manager / Name (ACO-…, DCO-…) / Month-Year /`.  
   - Multi-employee email → only that person’s attachment(s).  
   - Same filename again → dated copy (never overwrite).
4. **Upload / Manual Entry / Save .eml to Vault** — same extract core or direct filing.

## What's included
- **Email Inbox** — read, Extract Email, Auto Extract, Save .eml to vault, archive.
- **Upload** — PDF/DOCX/XLSX/images/`.eml`; same two-pass extract → review queue.
- **Files** — File Vault browse / upload / ZIP download (ACO/DCO folder names).
- **Employee Matcher** — CRUD + Excel import; ACO and DCO numbers.
- **Dashboard / Records** — roll-ups, leave buckets, approval sign-off.
- **Pipeline** — staged extracts, Compare & Fix, Retry from raw copy.

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
  api/       deps (RBAC) · routes/ (auth, admin, inbox, pipeline, employees, upload, files)
  services/  auth/ · employee/ · extract_email/ · extraction/ · llm/ · pipeline/ ·
             storage_provider/{local,s3,onedrive} · tasks (Celery)
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
Details: [`docs/EXTRACTION_TWO_PASS.md`](docs/EXTRACTION_TWO_PASS.md).

### Use your Postgres
```
docker compose -f docker-compose.postgres.yml up -d
# backend/.env:
DATABASE_URL=postgresql+asyncpg://timesheet:timesheet@localhost:5432/timesheet_db
# uncomment asyncpg in requirements.txt
```
Schema migrations: Alembic (`alembic upgrade head` — current head includes
`0022_employee_aco_number`). See [`docs/DATABASE_MIGRATIONS.md`](docs/DATABASE_MIGRATIONS.md).

### Other production notes
- Auth is built in (JWT + OTP / CAPTCHA / RBAC) — configure before exposing publicly.
- For large mailboxes, Graph **delta** sync + Celery; Auto Extract skips already-done threads.
- Storage swaps to S3 via `STORAGE_PROVIDER=s3` — see [`docs/DATA_STORAGE.md`](docs/DATA_STORAGE.md).

---

## Data model highlights

`all_employee_data`: employee_id, name, dco_number, account_manager, employee_email_id.

`timesheet_records`: extracted + matched identity, canonical leave buckets
(annual / remote / sick / unpaid / absent / public_holiday), `validation_status`
(verified | manual_review), `llm_summary` + `hr_flags`, `approval_detected`
(from screenshot) + `approval_status` (your sign-off), and `storage_folder`.
