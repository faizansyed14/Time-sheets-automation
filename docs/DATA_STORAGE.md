# Where does the data live? (Docker + AWS RDS + S3)

Yes — when you run `bash scripts/dev/start.sh` (or `prod`), the **application
runs inside Docker** (backend + Celery worker + frontend, plus local `db` and
`redis` containers unless you point them at managed services).

The app stores **three different kinds of data**, and they go to **three
different places**. This is the key thing to understand before connecting RDS
and S3.

---

## The three data stores

| # | What | Where it goes | Backend that owns it |
|---|------|---------------|----------------------|
| 1 | **Relational data** — users, employee records, ingested-email IDs + state, pipeline audit rows, timesheet records, app config | **PostgreSQL** | RDS (managed) or the local `db` container |
| 2 | **Filed timesheet files** — the File Vault: `Manager/Employee/Month/<file>` plus extracted `*.json` | **Object storage** | S3 (when `STORAGE_PROVIDER=s3`) or local disk |
| 3 | **Raw retry copies** — a private byte-for-byte copy of each ingested file so a *failed* file can be retried | **Local disk only** (`data/pipeline_raw/`) | Container filesystem / Docker volume |

### 1. Relational data → **RDS** (this is the critical data)

Everything the app persists as rows lives in Postgres:

- `auth_users` — application logins / roles
- `all_employee_data` — the authoritative employee matcher list
- `email_messages` — **the ingested-email mirror**: provider message IDs,
  accept/reject decision, ingested/archived state
- `pipeline_files` — **the full pipeline audit trail**: every file, its stage,
  status, failure code, and the `record_id` it produced
- `timesheet_records` — the extracted monthly leave data per employee
- `app_config` — admin-managed runtime settings (secrets encrypted)

When `DATABASE_URL` points at RDS, **all of this is stored in RDS** — not in the
project folder and not in a Docker volume. This is exactly the data you called
out as critical (ingested-email IDs, pipeline data, employee records), so RDS
(with automated backups + snapshots + Multi-AZ) is the right home for it.

### 2. Filed files → **S3**

When `STORAGE_PROVIDER=s3`, the File Vault is mapped onto S3 keys:

```
s3://<S3_BUCKET>/<S3_PREFIX>/<Manager>/<Employee>/<Month-Year>/<file>
```

Uploads, the extracted JSON, browsing, download, ZIP export, rename, and delete
all go straight to S3 (`backend/app/services/storage_provider/s3_provider.py`).
Nothing is written to the local `storage/` folder in this mode.

### 3. Raw retry copies → **local disk (NOT S3, NOT RDS)**

⚠️ **Important nuance.** The pipeline keeps a private copy of each original file
under `data/pipeline_raw/<pipeline_id>/` so a file that *failed* (e.g. employee
not yet in the matcher) can be retried after you fix the cause. This path uses
the local filesystem **regardless of `STORAGE_PROVIDER`** — it does **not** go
to S3.

- `pipeline_files.raw_path` stores the relative path to that local copy.
- These copies are **not business-critical**: they only enable the Retry button
  for failed/needs-review files. Successfully filed files already live in S3.

---

## So… project files, Docker volumes, RDS+S3 — which one?

With `DATABASE_URL` → RDS and `STORAGE_PROVIDER=s3`:

- ❌ **Not stored inside the project folder.** (The bind mount in dev is only
  for hot-reloading source code, not data.)
- ✅ **Relational data → RDS.** The local `db` container and its `pg_data`
  volume become unused — you should stop starting them (see below).
- ✅ **Filed files → S3.** The `backend_storage` volume / `storage/` folder
  becomes unused for filed files.
- ⚠️ **Raw retry copies → still on the container's local disk** under
  `data/pipeline_raw/`. In dev this lands in the `./backend` bind mount; in
  prod it lives on the container filesystem and is **lost on rebuild** unless
  you mount it (see recommendation). This only affects the ability to retry
  *failed* files, not your filed data.

In short: **RDS + S3 hold the real data; Docker volumes hold nothing important
once you switch; one local-disk folder keeps non-critical retry copies.**

---

## Recommended setup when using RDS + S3

1. **Point the app at RDS and S3** in `.env`:
   ```bash
   DATABASE_URL=postgresql+asyncpg://USER:PASS@my-db.xxxx.rds.amazonaws.com:5432/timesheet?ssl=require
   STORAGE_PROVIDER=s3
   S3_BUCKET=my-timesheets-prod
   S3_PREFIX=timesheets
   S3_REGION=us-east-1
   # leave AWS keys blank on EC2/ECS to use the instance IAM role
   ```

2. **Don't run the local `db` container** — it's dead weight (and a foot-gun if
   the app ever connects to it by accident). Either remove the `db` service +
   `pg_data` volume + the `depends_on: db` lines from your compose file, or
   start only the services you need:
   ```bash
   docker compose -f docker-compose.prod.yml --env-file .env up -d backend worker frontend
   ```
   (Redis is still needed for Celery + caching unless you use ElastiCache.)

3. **Persist the raw-retry folder in prod** (optional but recommended) so Retry
   survives a container rebuild — add a volume for `data/`:
   ```yaml
   backend:
     volumes:
       - backend_storage:/app/storage
       - backend_pipeline_raw:/app/data      # <-- add this
   # volumes: ... backend_pipeline_raw:
   ```
   Skip it if you're fine re-uploading any file that was mid-failure during a
   redeploy.

4. **Schema is managed by Alembic** — on startup the backend runs
   `alembic upgrade head` against RDS. See
   [DATABASE_MIGRATIONS.md](./DATABASE_MIGRATIONS.md).

---

## Quick mental model

```
                 ┌──────────────────────────── Docker host / ECS ───────────────────────────┐
   browser ──▶  │  frontend (nginx)  ──▶  backend (FastAPI)  ──▶  worker (Celery)            │
                 │                              │   │                                          │
                 └──────────────────────────────┼───┼──────────────────────────────────────────┘
                                                │   │
                  rows (users, employees,       │   │   files (timesheets + JSON)
                  emails, pipeline, records)    │   │
                                                ▼   ▼
                                         ┌──────────┐   ┌──────────┐
                                         │   RDS    │   │    S3    │   ← the durable, important data
                                         │ Postgres │   │  bucket  │
                                         └──────────┘   └──────────┘

   data/pipeline_raw/  ──▶  local disk / Docker volume   (non-critical retry copies only)
```
