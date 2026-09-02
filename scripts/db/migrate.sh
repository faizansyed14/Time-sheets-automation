#!/usr/bin/env bash
# Apply all pending Alembic migrations to the configured database.
#
# Targets whatever DATABASE_URL resolves to (backend/.env, the root .env, or a
# shell override) — so the SAME command works for local Postgres and AWS RDS:
#
#   # local (uses backend/.env or env vars)
#   bash scripts/db/migrate.sh
#
#   # explicit target (e.g. AWS RDS through an SSM tunnel on localhost:5432)
#   DATABASE_URL=postgresql+asyncpg://USER:PASS@localhost:5432/timesheet \
#     bash scripts/db/migrate.sh
#
# Inside Docker this runs automatically (see the backend `command:` in the
# compose files) — you only need this script for host-side / RDS migrations.
set -euo pipefail
cd "$(dirname "$0")/../../backend"

echo "▶ alembic upgrade head"
alembic upgrade head
echo "✓ database is at the latest migration"
