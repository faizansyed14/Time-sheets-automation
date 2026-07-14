#!/usr/bin/env bash
# Start the full PROD stack (postgres + redis + backend + worker + nginx).
#
# Needs a root .env — create one first:
#   ./scripts/create-env.sh PROD
# then replace every CHANGE_ME.
set -euo pipefail
cd "$(dirname "$0")/../.."

if [ ! -f .env ]; then
  echo "✗ No .env found. Create one first:" >&2
  echo "    ./scripts/create-env.sh" >&2
  echo "    # or:  ./scripts/create-env.sh PROD" >&2
  exit 1
fi
if grep -q "CHANGE_ME" .env; then
  echo "✗ Refusing to start: edit .env and replace all CHANGE_ME secrets first." >&2
  exit 1
fi
echo "▶ Starting prod stack (Docker)…"
docker compose -f docker-compose.prod.yml --env-file .env up --build -d
echo "✓ App: http://localhost  (nginx -> SPA + API proxy)"
echo "  Logs: docker compose -f docker-compose.prod.yml logs -f"
