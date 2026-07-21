# Timesheets Automation — Timesheet Intelligence Portal

Email-driven timesheet leave extraction with manager approval.

> **System architecture (auth, RBAC, OTP/CAPTCHA, Redis, Celery, LangChain,
> admin config, Docker, tests):** see **[docs/SYSTEM.md](docs/SYSTEM.md)**.
> Run the test suite with `bash scripts/test.sh`. Default admin: `admin` / `admin`.

## What's new in v2

1. **Multiple files per month (weekly / 15-day timesheets).** Some clients send
   a month as several files. Each file's extracted dates are stored as a
   *contribution* on the monthly record (`source_files`) and the record's
   buckets are the **union** of all contributions — a second file for the same
   employee + month **merges** instead of being treated as a duplicate.
   Re-uploading the same file replaces its own contribution (idempotent), and
   dates claimed by two *different* files raise a review flag. A manual edit of
   the record becomes the single source of truth for that month.

2. **Duplicate employee IDs across AUH and DXB.** `employee_id` is no longer
   globally unique — identity is **(employee_id, name)**. Matching resolves a
   shared ID by the name on the sheet (exact, then fuzzy); if the name can't
   pick a side the file fails as `ambiguous_id` instead of being filed under
   the wrong person. The Excel importer also keys on (ID, name), so AUH rows
   are no longer skipped as "duplicate ID".

3. **Pipeline tracker.** Every file that enters the pipeline (email accept OR
   upload) gets an audit row that walks through stages
   `received → protection_check → extraction → identification → matching →
   validation → filing → recorded` and ends as **success / needs_review /
   failed / resolved**. Failures carry an exact reason: `protected_pdf`,
   `llm_failed`, `name_not_found`, `month_not_found`, `employee_not_matched`,
   `ambiguous_id`, `id_name_mismatch`, `validation_mismatch`,
   `unsupported_type`, `storage_error`, … Nothing fails silently anymore.
   The Pipeline page has a **Resolve** button (human sign-off with a note) and
   a **Retry** button (re-runs the stored copy of the file after you fix the
   cause — e.g. after adding the missing employee to the matcher).

4. **New SaaS frontend.** Complete rewrite: sidebar app shell, KPI dashboard,
   split-pane inbox, drag-and-drop upload with per-file outcomes, pipeline
   tracker with stage timelines, employee matcher with AUH/DXB shared-ID
   indicators, three-pane file vault and a full record page with editable
   leave buckets. Works against the same mock/real provider seams.

You review incoming timesheet emails **inside the app**, accept or reject each one,
and accepted emails flow through an extraction pipeline that reads the leave data,
validates it, matches the employee against your employee matcher list, and files everything into a
per-employee / per-month folder. A dashboard rolls every employee up to a
green (clear) / yellow (needs review) status.

This build runs **end-to-end on mocks** out of the box (mock mailbox, mock LLM),
and includes your **real prompts + vision client + file conversion** ported in, ready to
activate with `EXTRACTION_ENGINE=vision`. Three clean seams swap mock → real:
email (Graph), extraction (your LLM), and DB (Postgres).

## What's included
- **Email Inbox** — read emails, preview attachments, Accept (→ pipeline) / Reject (→ archive).
- **Upload page** — drop PDF/DOCX/XLSX/images; runs the *same* pipeline as Accept.
- **Files page** — browse the `<Employee>/<Month-Year>/<files>` tree and create / rename /
  delete folders from the UI (backed by the storage provider; mirrors to OneDrive once connected).
- **Employee Matcher** — full CRUD over `all_employee_data` from the UI.
- **Dashboard** — per-employee green/yellow roll-up, eye button → monthly detail, year filter.
- **Employee record** — view & preview the stored source files (sheet, approval screenshot,
  result JSON) inline; **edit** the leave buckets/dates; **Mark verified** to clear review;
  **Approve / Not approved** sign-off; delete record. Editing re-runs validation automatically.
- **Validation** catches duplicate dates, dates in multiple categories, out-of-month dates,
  and header-month vs actual-dates mismatch — each shown as a plain-language flag.
- **Pipeline** — extract → validate → match to employee matcher (id → name → fuzzy) → file under
  `<Employee>/<Month-Year>/` → upsert record.

## Swappable providers (mock → real, all config-only)
| Concern | Config | Now | Later |
|---|---|---|---|
| Email | `EMAIL_PROVIDER` | `mock` | `graph` (Microsoft Graph) |
| Extraction | `EXTRACTION_ENGINE` | `mock` | `vision` (your real LLM) |
| File store | `STORAGE_PROVIDER` | `local` | `s3` (AWS S3) · `onedrive` |
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
1. **Email Inbox** → click an email → preview the body + attachments (PDF / DOCX / approval screenshot).
2. Click **Yes · Run extraction** (or **No · Archive**).
3. **Dashboard** → each employee shows green/yellow. Click the **eye** to open the monthly detail.
4. In the detail, set the **Approve / Not approved** sign-off; switch the **year** dropdown.

Mock emails are designed to exercise every path:
- clean + approved (Mohammed Ali, Jan)
- **duplicate date** → yellow (Priya, Jan)
- **out-of-month date** → yellow (Aisha, Jan)
- **fuzzy name match** "Mohd Ali" → "Mohammed Ali" (Feb)
- **one email → two people** fan-out (Sarah Khan forwards Carlos + Fatima)
- no approval screenshot (John, Feb)

---

## Architecture

Full architecture, project structure, auth/RBAC, Redis/Celery, LangChain, admin
config, Docker dev/prod and DB/storage portability (AWS RDS / S3) are documented
in **[docs/SYSTEM.md](docs/SYSTEM.md)**. Security & privacy posture (2FA for all
roles, the `admin`/`user`/`viewer` RBAC model, token revocation, OWASP/GDPR
mapping) is in **[docs/SECURITY.md](docs/SECURITY.md)**. High level:

```
backend/app/
  core/      config · database (PostgreSQL/asyncpg) · cache · celery_app · security · crypto
  models/    auth_users · app_config · all_employee_data · email · timesheet · pipeline
  api/       deps (RBAC) · routes/ (auth, admin, inbox, pipeline, employees, upload, files)
  services/  auth/ · config/ · employee/ · extraction/ · llm/ · pipeline/ ·
             storage_provider/{local,s3,onedrive} · tasks (Celery)
  seed/  alembic/ (migrations)  tests/
frontend/src/  api/client.ts · components/ · pages/ (Dashboard, Inbox, Upload,
               Pipeline, Employees, Files, Record, Login, admin/{Settings,Users})
```

### The pipeline (on Accept or Upload)
1. Read the manager-approval screenshot once → detected approved? + detail.
2. For **each** timesheet file (one email may carry several people — or several
   weekly sheets for the same person), a `PipelineFile` tracker row is created
   and the file walks through:
   - **protection check** — type accepted? PDF password-protected? (`protected_pdf`)
   - **extraction (LLM)** — engine errors become `llm_failed`, not 500s
   - **identification** — usable name/ID (`name_not_found`) and month (`month_not_found`)?
   - **matching** — employee_id **and** name vs `all_employee_data`; shared
     AUH/DXB IDs resolved by name, otherwise `ambiguous_id`
   - **validation** — duplicates, out-of-month, cross-file overlaps → flags
   - **filing** — sheet + approval + `extraction_result.json` under
     `<Manager>/<Employee>/<Month-Year>/`
   - **record** — upsert into the month's `TimesheetRecord`, merging this
     file's dates with any earlier weekly/15-day files for the same month
3. Mark the email **ingested**. (Reject just archives it — never reaches the pipeline.)
4. Failures appear on the **Pipeline** page with the exact stage + reason and
   can be **Resolved** (sign-off) or **Retried** (re-run from the stored copy).

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

### Activate your real LLM (already ported in)
Your prompts, vision client, and file conversion live in
`app/services/extraction/` (`parser.py`, `vision_client.py`, `file_processor.py`)
and the shared pipeline in `app/services/agents/full_email_extract.py`. To turn them on:
```
EXTRACTION_ENGINE=vision
OPENAI_API_KEY=sk-...
OPENAI_VISION_MODEL=gpt-4o        # or gpt-4.1 / gpt-5.4
```
The engine renders each document to ONE stitched JPEG (PDF via PyMuPDF;
DOCX/XLSX via LibreOffice `soffice` if installed, else a text render), sends
the extraction system prompt + per-batch request to your vision model, parses
the JSON, then runs **deterministic** leave/date validation + summary
(`validation.py` — no second LLM).

### Use your Postgres
```
docker compose -f docker-compose.postgres.yml up -d
# backend/.env:
DATABASE_URL=postgresql+asyncpg://timesheet:timesheet@localhost:5432/timesheet_db
# uncomment asyncpg in requirements.txt
```
The schema uses JSON columns + string PKs on PostgreSQL (asyncpg); point DATABASE_URL at AWS RDS to use a managed instance.
For production use Alembic migrations instead of `create_all`.

### Other production notes
- Auth is built in (JWT + OTP / CAPTCHA / RBAC) — configure before exposing publicly.
- For large mailboxes, ingestion already runs via Celery; Graph **delta** sync
  pulls only new mail between polls.
- Storage swaps to S3/MinIO via `STORAGE_PROVIDER=s3` and `services/storage_provider/`.

---

## Data model highlights

`all_employee_data`: employee_id, name, dco_number, account_manager, employee_email_id.

`timesheet_records`: extracted + matched identity, canonical leave buckets
(annual / remote / sick / unpaid / absent / public_holiday), `validation_status`
(verified | manual_review), `llm_summary` + `hr_flags`, `approval_detected`
(from screenshot) + `approval_status` (your sign-off), and `storage_folder`.
