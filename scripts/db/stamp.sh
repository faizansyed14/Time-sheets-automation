#!/usr/bin/env bash
# Mark an EXISTING database (whose tables were already created by the old
# create_all / upgrade_v2 startup path) as being at the Alembic baseline,
# WITHOUT trying to re-create the tables.
#
# Run this ONCE when adopting Alembic on a database that already has data
# (e.g. an RDS instance the app has been writing to):
#
#   DATABASE_URL=postgresql+asyncpg://USER:PASS@host:5432/timesheet \
#     bash scripts/db/stamp.sh
#
# Afterwards use scripts/db/migrate.sh for all future changes.
set -euo pipefail
cd "$(dirname "$0")/../../backend"

echo "▶ alembic stamp 0001_baseline"
alembic stamp 0001_baseline
echo "✓ existing database marked as up-to-date with the baseline migration"
