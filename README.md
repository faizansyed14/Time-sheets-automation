# Timesheet Intelligence Portal

Email-driven timesheet leave extraction with manager approval.

You review incoming timesheet emails **inside the app**, accept or reject each one,
and accepted emails flow through an extraction pipeline that reads the leave data,
validates it, matches the employee against your employee matcher list, and files everything into a
per-employee / per-month folder. A dashboard rolls every employee up to a
green (clear) / yellow (needs review) status.

This build runs **end-to-end on mocks** out of the box (SQLite, mock mailbox, mock LLM),
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
| File store | `STORAGE_PROVIDER` | `local` | `onedrive` (OneDrive/SharePoint) |
| Database | `DATABASE_URL` | SQLite | Postgres |

**Deleting mock entirely:** set the three providers to their real values, then remove
`app/seed/mock_data.py`, `app/services/email_provider/mock_provider.py`, and
`app/services/extraction/mock_engine.py`. Startup seeding no-ops if the mock data is gone,
and the factories only import a mock module when its provider is selected — so nothing else breaks.

---

## Quick start

### 1. Backend (terminal 1)
```bash
cd backend
python -m venv .venv && source .venv/bin/activate     # optional
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```
- API: http://localhost:8000
- Interactive docs: http://localhost:8000/docs

### 2. Frontend (terminal 2)
```bash
cd frontend
npm install
npm run dev
```
- App: http://localhost:5173  (Vite proxies `/api/*` to the backend automatically)

On first boot the backend creates a SQLite DB, seeds the mock employee matcher list
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

```
backend/
  app/
    core/         config + async DB (SQLite now, Postgres later)
    models/       Employee (all_employee_data), EmailMessage, TimesheetRecord
    schemas/      Pydantic response models
    services/
      email_provider/   base interface • mock_provider • graph_provider (stub)
      extraction/       base interface • mock_engine • validation (real checks)
      matching.py       employee match: exact id → exact name → fuzzy name
      storage.py        files -> storage/<Employee>/<Month-Year>/
      ingestion.py      the pipeline (runs on Accept)
    api/routes/   inbox • timesheets • employees
    seed/         mock_data (single source of truth) + employee matcher seeder
frontend/
  src/
    api/client.ts        typed API calls
    components/           Layout • RecordDetail • ui
    pages/               Dashboard • Inbox • EmployeeMonth
```

### The pipeline (on Accept)
1. Read the manager-approval screenshot once → detected approved? + detail.
2. For **each** timesheet attachment (one email may carry several people):
   - extract leave buckets → run validation (duplicates, out-of-month, overlaps) → green/yellow + summary
   - match identity against `all_employee_data`
   - file the sheet + approval screenshot + `extraction_result.json` under `<Employee>/<Month-Year>/`
   - upsert a `TimesheetRecord` (dedupe on employee + month + year)
3. Mark the email **ingested**. (Reject just archives it — never reaches the pipeline.)

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
Your prompts, vision client, and file conversion are ported into
`app/services/extraction/` (`parser.py`, `vision_client.py`, `file_processor.py`,
`vision_engine.py`). To turn them on:
```
EXTRACTION_ENGINE=vision
OPENAI_API_KEY=sk-...
EXTRACTION_MODEL=gpt-4o        # or gpt-4.1 / gpt-5.4
VALIDATION_MODEL=gpt-4o-mini
```
The engine renders the file to images (PDF via PyMuPDF; DOCX/XLSX via LibreOffice
`soffice` if installed, else a text render), sends `SYSTEM_PROMPT` + `EXTRACTION_PROMPT`
to your vision model (OpenAI file_id path for PDF/DOCX/XLSX, base64 images otherwise),
parses the JSON, runs deterministic validation plus the optional gpt-4o-mini text
cross-check, and reads the approval screenshot with a small vision call.
*If you want byte-identical behaviour, paste your current `parser.py` /
`vision_client.py` / `file_processor.py` over the ported copies — the engine calls the
same function names.*

### Use your Postgres
```
docker compose -f docker-compose.postgres.yml up -d
# backend/.env:
DATABASE_URL=postgresql+asyncpg://timesheet:timesheet@localhost:5432/timesheet_db
# uncomment asyncpg in requirements.txt
```
The schema uses JSON columns + string PKs, so it runs unchanged on SQLite or Postgres.
For production use Alembic migrations instead of `create_all`.

### Other production notes
- Add auth (the original app used JWT + a bootstrap admin) before exposing this.
- For large mailboxes, move ingestion to a Celery worker and use Graph **delta**
  queries so each poll pulls only new mail.
- Storage swaps to S3/MinIO by changing `services/storage.py` only.

---

## Data model highlights

`all_employee_data`: employee_id, name, dco_number, account_manager, employee_email_id.

`timesheet_records`: extracted + matched identity, canonical leave buckets
(annual / remote / sick / unpaid / absent / public_holiday), `validation_status`
(verified | manual_review), `llm_summary` + `hr_flags`, `approval_detected`
(from screenshot) + `approval_status` (your sign-off), and `storage_folder`.
