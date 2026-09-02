#!/usr/bin/env bash
# Seed the two fixed test employees (app/seed/seed_test_employees.py) —
# idempotent, safe to re-run after delete-employees.sh.
#
#   bash scripts/db/seed-test-employees.sh
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$ROOT"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.local.yml}"
if [ ! -f "$COMPOSE_FILE" ]; then
  COMPOSE_FILE="docker-compose.dev.yml"
fi
echo "▶ Seeding test employees…"
docker compose -f "$COMPOSE_FILE" cp \
  "$ROOT/backend/app/seed/seed_test_employees.py" \
  "backend:/app/app/seed/seed_test_employees.py"
docker compose -f "$COMPOSE_FILE" exec -T backend python -m app.seed.seed_test_employees
echo "✓ Test employees seeded"
