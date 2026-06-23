# Database Migrations (Alembic)

The schema for the Timesheet Intelligence Portal is versioned with
[Alembic](https://alembic.sqlalchemy.org/). This is the **single source of
truth** for the database structure in Docker, production, and AWS RDS.

> Why this matters: once the app has real data (ingested email IDs, pipeline
> audit rows, employee records), you can no longer "just recreate the tables".
> `Base.metadata.create_all` only ever *adds missing tables* — it never alters
> an existing one. Alembic gives you reviewable, ordered, reversible schema
> changes.

---

## File structure

```
backend/
├── alembic.ini                 # Alembic config (URL comes from settings, not here)
├── alembic/
│   ├── env.py                  # async-aware migration runner (reads DATABASE_URL)
│   ├── script.py.mako          # template for new migration files
│   └── versions/
│       └── 0001_baseline.py    # baseline: creates all current tables
└── app/
    ├── core/database.py        # Base + async engine (unchanged)
    └── models/                 # SQLAlchemy models = the desired schema
```

`alembic/env.py` imports `app.models` so every table is registered on
`Base.metadata`, and resolves the database URL in this priority order:

1. `alembic -x dburl=...` &nbsp;(one-off CLI override)
2. `$ALEMBIC_DATABASE_URL` &nbsp;(env var)
3. `settings.database_url` &nbsp;(the app's `DATABASE_URL` from `.env`)

So migrations always hit the **same** database the app uses — local, Docker, or
RDS — with no separate configuration.

---

## How it runs in Docker (the normal flow)

You don't run anything by hand. Both compose files start the backend with:

```yaml
command: sh -c "alembic upgrade head && uvicorn app.main:app ..."
environment:
  AUTO_CREATE_TABLES: "false"   # Alembic owns the schema
```

and the Celery worker waits for the backend to become healthy
(`depends_on: backend: condition: service_healthy`) so migrations finish before
any task runs. Only **one** service migrates, so there's no race.

```bash
bash scripts/dev/start.sh        # or scripts/prod/start.sh
# -> db starts -> backend runs `alembic upgrade head` -> uvicorn -> worker
```

`AUTO_CREATE_TABLES=false` tells `app/main.py` to skip the `create_all` path, so
Alembic is fully in charge.

> **Local quick-start without Alembic** is still supported: leave
> `AUTO_CREATE_TABLES=true` (the default in `backend/.env`) and the app creates
> missing tables on boot. Use this only for throwaway local databases.

---

## Everyday commands

Run from `backend/` (or use the wrappers in `scripts/db/`):

| Task | Command | Wrapper |
|------|---------|---------|
| Apply all migrations | `alembic upgrade head` | `bash scripts/db/migrate.sh` |
| Roll back one step | `alembic downgrade -1` | — |
| Create a migration from model changes | `alembic revision --autogenerate -m "msg"` | `bash scripts/db/revision.sh "msg"` |
| Mark an existing DB as current | `alembic stamp 0001_baseline` | `bash scripts/db/stamp.sh` |
| Show current DB revision | `alembic current` | — |
| Show history | `alembic history` | — |
| Preview SQL without applying | `alembic upgrade head --sql` | — |

---

## Adding a future schema change

1. Edit the model(s) under `backend/app/models/`.
2. Make sure your database is already at `head`
   (`bash scripts/db/migrate.sh`).
3. Autogenerate the migration:
   ```bash
   bash scripts/db/revision.sh "add termination_date to employees"
   ```
4. **Open the generated file** under `backend/alembic/versions/` and review it.
   Autogenerate is excellent for added/removed columns, indexes, and tables,
   but cannot infer renames, data backfills, or complex constraints — add those
   by hand using `op.execute(...)`.
5. Apply it:
   ```bash
   bash scripts/db/migrate.sh
   ```
6. Commit the new `versions/*.py` file alongside the model change. In Docker the
   migration is applied automatically on the next deploy.

---

## Adopting Alembic on a database that already has data

If you already ran the app (so tables exist from the old `create_all` path) and
now want Alembic to manage that **same** database — including an RDS instance
that already holds important data — do **not** run `upgrade head` first (the
tables already exist). Instead, stamp the baseline once:

```bash
# point at the existing DB (RDS via an SSM tunnel here)
DATABASE_URL=postgresql+asyncpg://USER:PASS@localhost:5432/timesheet \
  bash scripts/db/stamp.sh
```

This writes the `alembic_version` row without touching your tables. From then
on, `alembic upgrade head` only applies *new* migrations. The baseline
(`0001_baseline`) already reflects the current schema
(`timesheet_records.source_files` and the `uq_employee_id_name` composite
unique), so a stamped legacy DB is consistent.

---

## Running migrations against AWS RDS

Migrations are just SQL over a normal Postgres connection, so anything that can
reach RDS can run them:

- **From the deployed container (recommended):** nothing to do — the backend
  runs `alembic upgrade head` on startup against `DATABASE_URL` (your RDS
  endpoint). New migrations apply automatically on deploy.
- **From your laptop** (e.g. to stamp or pre-migrate), open a secure path to the
  private RDS instance first — an SSM port-forward or bastion tunnel to
  `localhost:5432` — then:
  ```bash
  DATABASE_URL=postgresql+asyncpg://USER:PASS@localhost:5432/timesheet?ssl=require \
    bash scripts/db/migrate.sh
  ```
  (`asyncpg` uses `?ssl=require`, not libpq's `sslmode`.)

Always take an RDS snapshot before applying a migration to production data.

> The old `app/migrations/upgrade_v2.py` best-effort patch script has been
> **removed** — Alembic's `0001_baseline` supersedes it.
