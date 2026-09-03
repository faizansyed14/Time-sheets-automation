#!/usr/bin/env bash
# Delete all all_employee_data rows (the employee matcher list).
# Keeps timesheet records, pipeline files, and reminder logs — see
# scripts/db/delete-records.sh / delete-pipeline.sh if you want those cleared
# too.
#
#   bash scripts/db/delete-employees.sh
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$ROOT"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.local.yml}"
if [ ! -f "$COMPOSE_FILE" ]; then
  COMPOSE_FILE="docker-compose.dev.yml"
fi
echo "▶ Clearing employee matcher list…"
# Seed modules are baked into the image at build time — sync this file so the
# script works without a full rebuild after adding/changing it locally.
docker compose -f "$COMPOSE_FILE" cp \
  "$ROOT/backend/app/seed/clear_employees.py" \
  "backend:/app/app/seed/clear_employees.py"
docker compose -f "$COMPOSE_FILE" exec -T backend python -m app.seed.clear_employees
echo "✓ Employees cleared"
