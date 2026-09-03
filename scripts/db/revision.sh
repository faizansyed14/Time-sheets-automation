#!/usr/bin/env bash
# Autogenerate a new Alembic migration after you change SQLAlchemy models.
#
#   bash scripts/db/revision.sh "add foo column to employees"
#
# It compares app/models against a LIVE database, so DATABASE_URL must point at
# a database that is already at `head` (run scripts/db/migrate.sh first). Always
# open the generated file under backend/alembic/versions/ and review it before
# committing — autogenerate cannot detect every change (renames, custom checks).
set -euo pipefail

if [ $# -lt 1 ]; then
  echo "usage: bash scripts/db/revision.sh \"short description of the change\"" >&2
  exit 1
fi

cd "$(dirname "$0")/../../backend"

echo "▶ alembic revision --autogenerate -m \"$1\""
alembic revision --autogenerate -m "$1"
echo "✓ new migration created under backend/alembic/versions/ — REVIEW it, then:"
echo "    bash scripts/db/migrate.sh   # apply it"
