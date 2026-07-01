# Deployment & Environment Guide

How to run this app as a shared **dev/staging** environment on EC2 (backed by
RDS + S3), keep it production-grade, and later stand up a separate **production**
environment without dev migrations ever touching prod.

---

## 1. Environment topology (recommended)

You are one developer with a teammate who tests timesheets. Use **three tiers**,
each with its OWN RDS + S3 so nothing bleeds across:

| Tier | Where it runs | RDS | S3 | Who uses it |
|------|---------------|-----|----|-------------|
| **local** | your laptop (`npm run dev` + `uvicorn --reload`, or this compose) | local Postgres *or* a dev RDS | dev bucket | you, fast iteration |
| **dev / staging** | a small EC2 (t3.medium) | **dev RDS** | **dev bucket** | you + the timesheet teammate, end-to-end testing |
| **prod** | its own EC2 (right-sized) | **prod RDS** | **prod bucket** | real users |

**Do you need a separate small EC2 for dev, or is "local = dev" enough?**
Keep **both**, they serve different jobs:
- **Local** is for *your* quick edit-run loop. But note: running locally against a
  *remote* RDS is slow (see §4) — for pure local speed, point local at a local
  Postgres container. Use local only for your own work.
- The **dev EC2** is the *shared* box your teammate hits. It must be same-Region/
  same-VPC as the dev RDS so it's fast, and it's where "does the whole flow work"
  is validated before prod. Don't ask your teammate to run Docker on their laptop.

**Prod is always its own EC2 + its own RDS + its own bucket.** Never share
infrastructure between dev and prod.

### Why this avoids future DB/migration problems
Alembic resolves its target database from **`DATABASE_URL`** (see
`backend/alembic/env.py` → `-x dburl=` › `ALEMBIC_DATABASE_URL` › `settings.database_url`).
`alembic upgrade`/`downgrade` only ever touches the DB that URL points at. So as
long as each tier has its **own `.env` with its own `DATABASE_URL`**, a dev
migration physically cannot reach prod. The rules that keep this true:
1. **Never** put a prod endpoint in a dev `.env`. Keep `.env` per-box, off git.
2. Separate buckets via `S3_BUCKET`; separate DBs via `DATABASE_URL`.
3. Ideally separate AWS accounts (or at minimum separate security groups + IAM),
   so a dev credential can't even see prod resources.
4. Test every migration on dev first; snapshot prod RDS before applying (see §8).

---

## 2. Do I need Docker volumes with external RDS + S3?

**No — for data.** With RDS holding all rows and S3 holding all files, the old
local `pg_data` and `backend_storage` volumes are pointless and have been
removed from `docker-compose.dev.yml`. There is **no local Postgres and no local
storage volume**.

**Yes — one small volume for Redis.** Redis is the Celery broker + cache +
rate-limit store. `redis_data` (appendonly) lets queued background jobs survive a
container restart. That's broker durability, not application data, and it's tiny.
If you don't care about losing in-flight jobs on restart, you can drop it too.

---

## 3. What's in the stack now (`docker-compose.dev.yml`)

```
redis    — Celery broker + cache (small appendonly volume)
backend  — FastAPI, uvicorn --workers ${UVICORN_WORKERS:-4}; runs Alembic on boot
worker   — Celery worker + beat (--concurrency ${CELERY_CONCURRENCY:-3})
nginx    — built SPA + reverse proxy to backend; published on 127.0.0.1:8080
```

- Same `.env` drives local and EC2. Tune `UVICORN_WORKERS` / `CELERY_CONCURRENCY`
  in `.env` without editing the compose file.
- **t3.large (2 vCPU):** `UVICORN_WORKERS=2`, `CELERY_CONCURRENCY=2`.
- The `nginx` service is the **built** SPA (minified, gzipped) — not the Vite dev
  server. Config lives at `nginx/nginx.conf` (mounted into the container).
- `nginx` is bound to `127.0.0.1:8080` so a **host nginx + certbot** owns 80/443
  and terminates HTTPS (see §7).

---

## 4. Why GETs feel slow — and what actually fixes it

Three independent causes; fix all three:

1. **Network latency to RDS/S3 (biggest one for local).** Running Docker on your
   laptop against a *remote* RDS means every SQL round-trip crosses the public
   internet (tens of ms each). A page that issues several queries stacks that up.
   → On the **dev EC2 in the same VPC/AZ as RDS**, round-trips are sub-millisecond.
   This alone makes "fetch data and render" dramatically faster. Same for S3.
2. **Too few workers → head-of-line blocking.** One uvicorn process serializes
   CPU-heavy work (DOCX→PDF, OCR, image render); a slow request blocks every other
   GET behind it. → `UVICORN_WORKERS=4` (≈ vCPU count) lets GETs run in parallel.
3. **Vite dev bundle.** The dev server ships unminified modules; the prod build
   nginx serves is far lighter. → Already fixed (frontend uses the `prod` target).

Tuning knobs (in `.env`): `UVICORN_WORKERS`, `CELERY_CONCURRENCY`, `DB_POOL_SIZE`,
`DB_MAX_OVERFLOW`, `CACHE_TTL_SECONDS`. Keep
`workers × (DB_POOL_SIZE + DB_MAX_OVERFLOW) + celery` **under** your RDS
`max_connections` (a db.t3.micro allows ~87; the defaults 4×(5+5)=40 leave room).
If you need more DB concurrency than RDS allows, put **RDS Proxy** in front.

---

## 5. Create the EC2 instance and wire RDS + S3 securely

### 5.1 IAM role for S3 (no keys on the box)
The S3 provider auto-uses the instance role when the key fields are blank
(`backend/app/services/storage_provider/s3_provider.py`). Create role
`timesheet-dev-ec2` with least privilege for your **dev** bucket only:
```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": ["s3:GetObject","s3:PutObject","s3:DeleteObject","s3:ListBucket"],
    "Resource": ["arn:aws:s3:::your-dev-bucket","arn:aws:s3:::your-dev-bucket/*"]
  }]
}
```

### 5.2 Launch EC2
- **Ubuntu 22.04/24.04 LTS** (recommended — matches §6/§7 commands) or Amazon
  Linux 2023 (use §6 Amazon Linux Docker steps). **t3.large** is a good dev size.
  30 GB gp3 root (box is stateless).
- **Same VPC as the dev RDS** (pick a subnet in the same AZ if you can).
- Attach the `timesheet-dev-ec2` IAM instance role (S3 access).
- Security group **`sg-app-dev`** (do **not** attach the RDS security group to EC2).
  Inbound:
  - `22` from **your IP only**
  - `80` and `443` from `0.0.0.0/0`
  - nothing else (8080 stays private — bound to 127.0.0.1 in compose)

### 5.3 Lock RDS to the app SG only
On the **dev RDS** security group, one inbound rule:
```
PostgreSQL 5432   Source: sg-app-dev   (NOT 0.0.0.0/0, NOT your home IP)
```
RDS **Public access = No**. Now RDS is reachable only from instances in `sg-app-dev`.

---

## 6. SSH in, install Docker, clone, run

### 6.1 Install Docker + Compose

**Ubuntu** (SSH user `ubuntu`):

```bash
ssh ubuntu@<ec2-public-ip>

curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER && newgrp docker
sudo apt-get install -y docker-compose-plugin git
docker compose version
```

**Amazon Linux 2023** (SSH user `ec2-user`) — `get.docker.com` does not support
`amzn`; use `dnf` and install the Compose plugin manually:

```bash
ssh -i timesheet-dev-key.pem ec2-user@<ec2-public-ip>

sudo dnf update -y
sudo dnf install -y docker git
sudo systemctl enable --now docker
sudo usermod -aG docker ec2-user && newgrp docker

sudo mkdir -p /usr/local/lib/docker/cli-plugins
sudo curl -SL https://github.com/docker/compose/releases/download/v2.29.2/docker-compose-linux-x86_64 \
  -o /usr/local/lib/docker/cli-plugins/docker-compose
sudo chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
docker compose version
```

### 6.2 Clone, configure, start (both OS)

```bash
git clone https://github.com/faizansyed14/time-sheets-automation.git
cd time-sheets-automation
git checkout dev
git pull origin dev          # get latest (nginx service, celery fix, etc.)

cp .env.dev .env
nano .env
```

Required in `.env`:

| Variable | Value |
|----------|--------|
| `DATABASE_URL` | `postgresql+asyncpg://USER:PASS@<rds-endpoint>:5432/<db>?ssl=require` |
| `STORAGE_PROVIDER` | `s3` |
| `S3_BUCKET` / `S3_REGION` | your dev bucket + region |
| `EMAIL_PROVIDER` | `graph` (+ `GRAPH_*` if using real inbox) |
| `JWT_SECRET` | strong random secret |
| `UVICORN_WORKERS` | `2` on t3.large |
| `CELERY_CONCURRENCY` | `2` on t3.large |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | **blank** on EC2 (use IAM role) |
| `CORS_ORIGINS` | `["https://dev.yourdomain.com"]` or temp `["http://<public-ip>"]` |

Generate a JWT secret:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

Start the stack (migrations run automatically on backend boot):

```bash
docker compose -f docker-compose.dev.yml --env-file .env up -d --build
docker compose -f docker-compose.dev.yml ps
docker compose -f docker-compose.dev.yml logs -f backend   # watch Alembic
```

Verify on the box:

```bash
curl -fsS http://localhost:8080/health
docker compose -f docker-compose.dev.yml logs nginx --tail 30
```

You should see four services: `redis`, `backend`, `worker`, `nginx`.

---

## 7. HTTPS with Let's Encrypt (host nginx + certbot)

Point a DNS A-record (`dev.yourdomain.com`) at the EC2's Elastic IP, then:
```bash
sudo apt-get update && sudo apt-get install -y nginx
sudo cp nginx/host-tls.conf.example /etc/nginx/sites-available/timesheets
sudo nano /etc/nginx/sites-available/timesheets     # set server_name
sudo ln -s /etc/nginx/sites-available/timesheets /etc/nginx/sites-enabled/timesheets
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx

sudo apt-get install -y certbot python3-certbot-nginx
sudo certbot --nginx -d dev.yourdomain.com --redirect --agree-tos -m you@yourcompany.com
sudo certbot renew --dry-run     # confirm auto-renewal works
```
certbot rewrites the site to listen on 443, redirects 80→443, and installs a
systemd timer that renews automatically. Host nginx → `127.0.0.1:8080` (the
`nginx` container: SPA + `/api` proxy) → `backend`. Set
`CORS_ORIGINS=["https://dev.yourdomain.com"]` in `.env` and restart the stack.

On **Amazon Linux**, replace `apt-get` with `dnf install nginx certbot
python3-certbot-nginx` and use `/etc/nginx/conf.d/` instead of
`sites-available` / `sites-enabled`.

---

## 8. Backups & migration discipline for PROD

### RDS
- **Automated backups**: retention 7–35 days (enables point-in-time recovery).
- **Manual snapshot before every prod migration/deploy** — this is your instant
  rollback if a migration goes wrong.
- **Multi-AZ** for the prod instance (automatic failover) — optional, costs more.
- Deletion protection ON.

### S3
- **Versioning ON** (recover overwritten/deleted timesheets).
- Lifecycle rule to expire old noncurrent versions (cost control).
- Optional cross-Region replication for DR.

### Safe prod-migration checklist
1. Merge the change to `main` and build the prod image.
2. Run the migration on **dev** first — confirm it applies cleanly.
3. **Snapshot the prod RDS** (manual).
4. Deploy to prod; `bootstrap_migrations.py` runs `alembic upgrade head` against
   the **prod** `DATABASE_URL` only.
5. If it fails: restore the snapshot (or `alembic downgrade` if the down-revision
   is safe — but restore-from-snapshot is the reliable path).

> A dev `alembic upgrade`/`downgrade` **cannot** affect prod as long as dev's
> `.env` `DATABASE_URL` points at the dev RDS. That separation is the whole game —
> guard the `.env` files.

### "Mirror prod data into dev"
To seed dev with prod data for realistic testing: take a **prod RDS snapshot →
restore it as the dev instance**, and `aws s3 sync s3://prod-bucket
s3://dev-bucket`. Do this as a one-off/periodic copy — never point dev at the
prod resources directly.

---

## 9. Everyday ops

```bash
# logs / status
docker compose -f docker-compose.dev.yml ps
docker compose -f docker-compose.dev.yml logs -f backend
docker compose -f docker-compose.dev.yml logs -f worker
docker compose -f docker-compose.dev.yml logs nginx --tail 50

# pull latest dev + rebuild
git pull origin dev
docker compose -f docker-compose.dev.yml --env-file .env up -d --build

# restart nginx after editing nginx/nginx.conf (no image rebuild needed)
docker compose -f docker-compose.dev.yml restart nginx

# run a migration manually (targets DATABASE_URL in .env)
docker compose -f docker-compose.dev.yml exec backend alembic upgrade head

# connect to RDS directly
psql "$(grep -E '^DATABASE_URL' .env | cut -d= -f2- | sed 's#+asyncpg##')"

# stop (Redis broker volume is kept; add --volumes to wipe it)
docker compose -f docker-compose.dev.yml down
```
