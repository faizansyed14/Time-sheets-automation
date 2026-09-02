#!/usr/bin/env bash
# Start the DEV/STAGING stack (redis + backend + worker + built frontend/nginx)
# via Docker. Uses external AWS RDS + S3 — no local Postgres / storage volume.
#
# Needs a root .env — create one first:
#   ./scripts/create-env.sh DEV
set -euo pipefail
cd "$(dirname "$0")/../.."

if [ ! -f .env ]; then
  echo "✗ No .env found. Create one first:" >&2
  echo "    ./scripts/create-env.sh" >&2
  echo "    # or:  ./scripts/create-env.sh DEV" >&2
  exit 1
fi

echo "▶ Starting dev/staging stack (Docker) — RDS + S3, prod-style build…"
docker compose -f docker-compose.dev.yml --env-file .env up --build -d
echo "✓ App (local):  http://localhost:8080   (single entry: SPA + API via nginx)"
echo "  On EC2, a host nginx + certbot terminates HTTPS in front of :8080 —"
echo "  see docs/DEPLOYMENT.md."
echo "  Logs: docker compose -f docker-compose.dev.yml logs -f"
