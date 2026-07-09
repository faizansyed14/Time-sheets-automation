# Dev (local) vs Prod environments — RDS + S3

Two **completely separate** sets of AWS resources, so testing from your laptop can
never touch production data:

| | **DEV** (run locally now) | **PROD** (later, on EC2/ECS) |
|---|---|---|
| App runs on | your laptop | EC2 / ECS / ECR image |
| RDS | small `timesheets-dev` instance | separate `timesheets-prod` instance |
| S3 bucket | `mycompany-timesheets-dev` | `mycompany-timesheets-prod` |
| AWS auth | **IAM user access key** (laptop has no role) | **IAM role** on the instance (no keys) |
| RDS reachability | public **but locked to your IP** (or SSM tunnel) | private, inside the VPC only |
| Config file | `backend/.env` | root `.env` (read by `docker-compose.prod.yml`) |

> Follow `docs/AWS_SETUP.md` for the detailed bucket/policy/instance steps — this
> doc is the **two-environment wiring** on top of it. Create each resource twice
> (once per env) with the names above.

---

## DEV — connect to a small RDS + S3 from your laptop

### 1. Small dev RDS
- Engine **PostgreSQL 16**, class **`db.t4g.micro`** (cheapest), 20 GB, **Single-AZ**.
- Initial DB name: `timesheet`.
- **Public access: YES**, BUT the security group inbound `5432` is restricted to
  **your current public IP only** (`x.x.x.x/32`) — never `0.0.0.0/0`. Set
  `rds.force_ssl=1`.
  - *(More secure alternative: keep it private and reach it via an SSM tunnel as
    in AWS_SETUP.md §B2; then use `localhost:5432` in the URL.)*
- Create the least-privilege user (as master, see AWS_SETUP.md §B3):
  ```sql
  CREATE USER ts_app_dev WITH PASSWORD 'DEV_DB_PASSWORD';
  GRANT CONNECT ON DATABASE timesheet TO ts_app_dev;
  GRANT USAGE, CREATE ON SCHEMA public TO ts_app_dev;
  GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO ts_app_dev;
  ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO ts_app_dev;
  ```

### 2. Dev S3 bucket + dev IAM user
- Bucket `mycompany-timesheets-dev`, **Block all public access ON**, encryption ON.
- IAM policy `timesheets-s3-rw-dev` scoped to that bucket's `timesheets/*`
  (same JSON as AWS_SETUP.md §A2, just the dev bucket ARN).
- IAM **user** `timesheets-app-dev` (programmatic only) → attach the policy →
  create an access key. Configure it as a **named profile** so the secret never
  sits in a file:
  ```bash
  aws configure --profile timesheets-dev     # key id + secret, region us-east-1
  ```

### 3. `backend/.env` for local dev
```bash
cp .env.example backend/.env     # then edit:
```
```bash
ENVIRONMENT=dev
AUTO_CREATE_TABLES=false          # use Alembic (set true if you want zero-setup)

# --- Dev RDS (asyncpg uses ?ssl=require, NOT sslmode) ---
DATABASE_URL=postgresql+asyncpg://ts_app_dev:DEV_DB_PASSWORD@your-dev-db.xxxx.us-east-1.rds.amazonaws.com:5432/timesheet?ssl=require

# --- Dev S3 ---
STORAGE_PROVIDER=s3
S3_BUCKET=mycompany-timesheets-dev
S3_PREFIX=timesheets
S3_REGION=us-east-1
# Leave the keys BLANK and use the named profile (export AWS_PROFILE=timesheets-dev).
# Or paste the dev access key here. boto3 only uses these if BOTH are set.
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=

# --- local infra: no Redis/worker needed ---
CELERY_TASK_ALWAYS_EAGER=true     # run tasks inline
CACHE_ENABLED=true                # falls back to in-memory if Redis is absent
EMAIL_PROVIDER=mock
EXTRACTION_ENGINE=mock            # switch to "vision" to test the real LLM
AUTH_ENABLED=true
JWT_SECRET=dev-secret-change-me-to-something-32-bytes-or-more
CORS_ORIGINS=["http://localhost:5173","http://127.0.0.1:5173"]
```

### 4. Run it locally
```bash
# pick up the dev S3 profile (only if you left the keys blank above)
export AWS_PROFILE=timesheets-dev

# migrate the dev RDS, then start the API
cd backend
pip install -r requirements.txt
alembic upgrade head            # or: bash ../scripts/db/migrate.sh  (from repo root)
uvicorn app.main:app --reload --port 8000

# in another terminal: the frontend
cd frontend && npm install && npm run dev
```
Check `http://localhost:8000/health`, log in, upload a timesheet → it lands in the
**dev** bucket and the row in the **dev** RDS. No Redis or Celery worker required.

> You do **not** need Docker for dev-from-local. (If you prefer Docker, use the
> prod compose and remove the `db` service — but plain `uvicorn` is simpler here.)

---

## PROD — on EC2/ECS with a separate RDS + S3

Everything is the **prod** copy of the resources, and the app uses an **IAM role**
instead of keys.

### 1. Prod resources
- RDS `timesheets-prod`: **Public access = NO**, private subnets, SG allows `5432`
  only from the app's SG, Multi-AZ + backups, `rds.force_ssl=1`. User `ts_app`.
- Bucket `mycompany-timesheets-prod` (Block public access ON) + policy
  `timesheets-s3-rw-prod` scoped to its `timesheets/*`.

### 2. IAM role (no static keys)
- Create an IAM **role** for the EC2 instance / ECS task, attach
  `timesheets-s3-rw-prod`, and assign it to the running container/instance.
- Because the role is attached, you set **no `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`** —
  boto3 picks up the role automatically (the code already supports this).

### 3. Root `.env` for prod (read by `docker-compose.prod.yml`)
```bash
cp .env.example .env     # apply PROD profile block, then edit:
```
```bash
ENVIRONMENT=prod
AUTO_CREATE_TABLES=false

# --- Prod RDS ---
DATABASE_URL=postgresql+asyncpg://ts_app:PROD_DB_PASSWORD@your-prod-db.xxxx.us-east-1.rds.amazonaws.com:5432/timesheet?ssl=require

# --- Prod S3 (NO keys — the EC2/ECS IAM role provides them) ---
STORAGE_PROVIDER=s3
S3_BUCKET=mycompany-timesheets-prod
S3_PREFIX=timesheets
S3_REGION=us-east-1
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=

# --- prod infra ---
CELERY_TASK_ALWAYS_EAGER=false
REDIS_URL=redis://redis:6379/0
JWT_SECRET=GENERATE_A_LONG_RANDOM_SECRET
# generate: python -c "import secrets; print(secrets.token_urlsafe(48))"
EMAIL_PROVIDER=graph              # or mock
EXTRACTION_ENGINE=vision          # or mock
CORS_ORIGINS=["https://your-domain.com"]
```

### 4. Deploy
- Build/push the image (ECR), or build on the box.
- **Drop the local `db` service** from `docker-compose.prod.yml` (you're on RDS) —
  remove the `db:` service, `pg_data` volume, and `depends_on: db` lines, **or**
  start only what you need:
  ```bash
  docker compose -f docker-compose.prod.yml --env-file .env up -d backend worker frontend
  ```
- The backend runs `alembic upgrade head` against **prod** RDS on boot.
  **Snapshot prod RDS before each deploy** that includes a new migration.
- Redis: a `redis` container or ElastiCache.

---

## Promoting dev → prod safely

- The **only** things that differ are `DATABASE_URL`, `S3_BUCKET`, the AWS auth
  method, and a few infra flags. Same image, same migrations.
- Because dev and prod point at **different RDS instances and different buckets**,
  nothing you do in dev can affect prod.
- **Always confirm which `.env` you're launching with** before `up` — that single
  file decides which database and bucket the app talks to.
- Take an RDS snapshot before applying migrations to prod.

---

## Per-environment security checklist

**DEV**
- [ ] Dev RDS SG inbound `5432` = **your IP /32 only** (or private + SSM tunnel).
- [ ] `rds.force_ssl=1` and `?ssl=require` in the URL.
- [ ] Dev IAM user scoped to the **dev** bucket/prefix only; key kept in an AWS
      **named profile**, not committed.
- [ ] App connects as `ts_app_dev` (non-superuser), not the master user.

**PROD**
- [ ] RDS **not public**; SG locked to the app SG; private subnets.
- [ ] **IAM role** on EC2/ECS → **no AWS keys** in `.env`.
- [ ] S3 bucket Block-public-access ON; policy scoped to the **prod** bucket/prefix.
- [ ] App connects as `ts_app` (non-superuser); secrets ideally in AWS Secrets Manager.
- [ ] `.env` never committed (already git-ignored).
