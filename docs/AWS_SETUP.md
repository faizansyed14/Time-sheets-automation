# AWS Setup — RDS (PostgreSQL) + S3, securely & least-privilege

Goal: run the app against **Amazon RDS** (the important relational data) and
**Amazon S3** (the filed timesheets) **without** making anything public or handing
out broad permissions. Every credential below is scoped to exactly one bucket /
one database and only the actions the app actually uses.

> The app is already built for this — it's purely configuration. See the code
> hooks: `DATABASE_URL` (`app/core/database.py`), the S3 provider
> (`app/services/storage_provider/s3_provider.py`), and the Alembic flow
> (`docs/DATABASE_MIGRATIONS.md`).

Security model in one line: **RDS stays private** (reachable only inside the VPC
or through a tunnel), **S3 blocks all public access**, and the app authenticates
with **least-privilege credentials** — ideally an **IAM role** in production (no
static keys at all).

---

## Part A — Amazon S3 (file storage)

### A1. Create the bucket (private + encrypted)
Console → S3 → **Create bucket**:
- Name: `mycompany-timesheets-prod` (globally unique)
- Region: e.g. `us-east-1` (use the SAME region as RDS to avoid egress cost)
- **Block ALL public access: ON** (leave every box checked)
- **Default encryption: ON** (SSE-S3, or SSE-KMS for stricter control)
- Bucket Versioning: optional but recommended (protects against accidental overwrite/delete)

### A2. Create a least-privilege IAM policy
IAM → Policies → **Create policy** → JSON. This grants **only** list + read/write/delete
**inside `timesheets/`** of **this one bucket** — nothing else in your account:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ListOnlyThisBucketPrefix",
      "Effect": "Allow",
      "Action": ["s3:ListBucket"],
      "Resource": "arn:aws:s3:::mycompany-timesheets-prod",
      "Condition": { "StringLike": { "s3:prefix": ["timesheets/*", "timesheets"] } }
    },
    {
      "Sid": "ObjectRWInPrefix",
      "Effect": "Allow",
      "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
      "Resource": "arn:aws:s3:::mycompany-timesheets-prod/timesheets/*"
    }
  ]
}
```

Name it `timesheets-s3-rw`. These five actions are exactly what the provider
calls (`ListBucket`, `GetObject`, `PutObject`, `DeleteObject`, plus `copy_object`
which uses Get+Put). No `s3:*`, no other buckets.

### A3. Give the app credentials (pick ONE)

**Option 1 — IAM Role (best; use in production on EC2/ECS/EKS):**
- Create an IAM **role**, attach `timesheets-s3-rw`, and assign it to the
  instance/task running the backend.
- Set **no AWS keys** in `.env` — boto3 uses the role automatically
  (the code already supports this).

**Option 2 — IAM User access key (for local dev or non-AWS hosting):**
- IAM → Users → **Create user** `timesheets-app` → **no console access**
  (programmatic only) → attach `timesheets-s3-rw`.
- Create an **access key** → save the Access Key ID + Secret. This is the only
  time the secret is shown.

### A4. Test from your laptop before wiring the app
```bash
# uses the access key (configure a named profile so it's not global)
aws configure --profile timesheets        # paste key id + secret, region us-east-1

aws s3 ls s3://mycompany-timesheets-prod/ --profile timesheets
echo "ok" | aws s3 cp - s3://mycompany-timesheets-prod/timesheets/_healthcheck.txt --profile timesheets
aws s3 rm s3://mycompany-timesheets-prod/timesheets/_healthcheck.txt --profile timesheets
```
If list/put/delete succeed and nothing else is reachable, the policy is correct.

---

## Part B — Amazon RDS (PostgreSQL)

### B1. Create the instance (private, SSL-enforced)
Console → RDS → **Create database**:
- Engine: **PostgreSQL 16** (matches the app's `postgres:16` dev image)
- Templates: Production (Multi-AZ) for real use; Dev/Test to start
- Credentials: set a **master** username/password — this is for admin only, the
  app will NOT use it (see B3)
- Instance/storage: as needed; enable **storage autoscaling**
- Connectivity:
  - **Public access: NO** ← critical
  - VPC + subnet group: your private subnets
  - **Security group**: create one that allows inbound `5432` **only** from your
    app's security group (or a bastion SG). Never `0.0.0.0/0`.
- Additional config:
  - Initial database name: `timesheet`
  - **Enable automated backups** + snapshots
- After creation, set the parameter **`rds.force_ssl = 1`** (require TLS) on the
  DB parameter group.

Note the endpoint: `your-db.xxxx.us-east-1.rds.amazonaws.com:5432`.

### B2. Reach the private DB to do admin (no public exposure)
Use **SSM Session Manager port-forwarding** through a bastion/EC2 in the VPC
(preferred — nothing is exposed to the internet):
```bash
aws ssm start-session --target i-0yourbastion \
  --document-name AWS-StartPortForwardingSessionToRemoteHost \
  --parameters '{"host":["your-db.xxxx.us-east-1.rds.amazonaws.com"],"portNumber":["5432"],"localPortNumber":["5432"]}'
```
Now `localhost:5432` tunnels to RDS. (A bastion host with an SSH tunnel works too;
just keep the RDS security group restricted to the bastion.)

### B3. Create a least-privilege application user (NOT the master)
Connect as the master user (through the tunnel) and create a dedicated app role
that can only touch the `timesheet` database:
```bash
psql "host=localhost port=5432 dbname=timesheet user=MASTER_USER sslmode=require"
```
```sql
-- dedicated app login
CREATE USER ts_app WITH PASSWORD 'STRONG_RANDOM_DB_PASSWORD';

GRANT CONNECT ON DATABASE timesheet TO ts_app;
GRANT USAGE, CREATE ON SCHEMA public TO ts_app;          -- CREATE lets Alembic build tables
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO ts_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO ts_app;
```
`ts_app` can read/write data and run migrations, but is **not** a superuser and
can't touch other databases.

> **Stricter variant (optional):** if you don't want the runtime user to have
> DDL, drop `CREATE` from its grant, create a separate `ts_migrator` role with
> schema ownership, run `alembic upgrade head` as `ts_migrator` out-of-band
> (`scripts/db/migrate.sh` with a migrator `DATABASE_URL`), and keep
> `AUTO_CREATE_TABLES=false`. The app's startup `alembic upgrade head` is then a
> no-op (read-only) once the schema is current.

### B4. Test the connection + apply migrations
```bash
# quick connectivity check
psql "host=localhost port=5432 dbname=timesheet user=ts_app sslmode=require" -c "select 1;"

# apply the schema (Alembic) — run from repo root
DATABASE_URL='postgresql+asyncpg://ts_app:STRONG_RANDOM_DB_PASSWORD@localhost:5432/timesheet?ssl=require' \
  bash scripts/db/migrate.sh
```
(`asyncpg` uses `?ssl=require` in the URL — **not** libpq's `sslmode`.)

---

## Part C — What to put in `.env` after creation

Add/replace these keys (start from `.env.prod`). Use the **RDS endpoint** for the
host, the scoped IAM user for S3:

```bash
ENVIRONMENT=prod

# Alembic owns the schema in prod/RDS
AUTO_CREATE_TABLES=false

# ---- RDS (PostgreSQL) ----
# NOTE: asyncpg uses ?ssl=require (not sslmode). User is the least-privileged ts_app.
DATABASE_URL=postgresql+asyncpg://ts_app:STRONG_RANDOM_DB_PASSWORD@your-db.xxxx.us-east-1.rds.amazonaws.com:5432/timesheet?ssl=require

# ---- S3 ----
STORAGE_PROVIDER=s3
S3_BUCKET=mycompany-timesheets-prod
S3_PREFIX=timesheets
S3_REGION=us-east-1
# Programmatic IAM user keys. OMIT BOTH on EC2/ECS to use the attached IAM role.
AWS_ACCESS_KEY_ID=AKIAXXXXXXXXXXXXXXXX
AWS_SECRET_ACCESS_KEY=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
# S3_ENDPOINT_URL=        # leave unset for real AWS (only for MinIO/S3-compatible)
```

You can now **delete** the local-only Postgres keys that fed the Docker `db`
container — they're unused against RDS:
```
# POSTGRES_USER / POSTGRES_PASSWORD / POSTGRES_DB  -> remove (RDS, not local db)
```

---

## Part D — Wiring it into Docker (important gotchas)

1. **Use the prod compose** (`docker-compose.prod.yml`): its `backend` service
   reads `DATABASE_URL` from `.env`. The **dev** compose hard-codes
   `DATABASE_URL=...@db:5432/...` in the service `environment:`, which would
   **override** your RDS URL — so don't use the dev compose against RDS.
2. **Drop the local `db` service** when using RDS, or it's dead weight and a
   foot-gun. Remove the `db:` service, the `pg_data` volume, and every
   `depends_on: db` line — or just start the services you need:
   ```bash
   docker compose -f docker-compose.prod.yml --env-file .env up -d backend worker frontend
   ```
3. **Migrations run automatically**: the backend command is
   `alembic upgrade head && uvicorn ...`, so on boot it migrates RDS using
   `DATABASE_URL`. Take an RDS snapshot before deploying a new migration.
4. Redis is still needed (Celery + cache). Use a `redis` container or ElastiCache.

---

## Security checklist (the "don't expose / least access" rules)

- [ ] S3 bucket has **Block all public access = ON**; no public bucket policy.
- [ ] S3 credential is scoped to **one bucket + `timesheets/` prefix**, 5 actions only — no `s3:*`.
- [ ] Prefer an **IAM role** in production → **no static keys in `.env`** at all.
- [ ] RDS **Public access = NO**; security group allows `5432` only from the app/bastion SG (never `0.0.0.0/0`).
- [ ] RDS enforces TLS (`rds.force_ssl=1`) and the URL has `?ssl=require`.
- [ ] App connects as **`ts_app`**, a non-superuser scoped to the `timesheet` DB — **never the master user**.
- [ ] Secrets live in `.env` (git-ignored) or, better, **AWS Secrets Manager**; rotate keys/passwords periodically.
- [ ] `.env` is never committed (already in `.gitignore`).
- [ ] Reach RDS from laptops only via **SSM tunnel / bastion**, not a public endpoint.
