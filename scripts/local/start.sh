#!/usr/bin/env bash
# Start the LOCAL stack (Postgres + Redis + backend + worker + nginx) — fully
# self-contained in Docker volumes. No AWS RDS / S3 needed.
#
# Needs a root .env — create one first:
#   ./scripts/create-env.sh          # pick LOCAL / DEV / PROD interactively
#   ./scripts/create-env.sh LOCAL
set -euo pipefail
cd "$(dirname "$0")/../.."

if [ ! -f .env ]; then
  echo "✗ No .env found. Create one first:" >&2
  echo "    ./scripts/create-env.sh" >&2
  echo "    # or:  ./scripts/create-env.sh LOCAL" >&2
  exit 1
fi

echo "▶ Starting local stack (Docker volumes for db + files)…"
docker compose -f docker-compose.local.yml --env-file .env up --build -d
echo "✓ App:  http://localhost:8080   Login: admin / admin"
echo "  Logs: docker compose -f docker-compose.local.yml logs -f"
