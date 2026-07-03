#!/usr/bin/env bash
# Start the LOCAL stack (Postgres + Redis + backend + worker + nginx) — fully
# self-contained in Docker volumes. No AWS RDS / S3 needed.
set -euo pipefail
cd "$(dirname "$0")/../.."
if [ ! -f .env ]; then
  echo "▶ No .env found — creating one from .env.local"
  cp .env.local .env
fi
echo "▶ Starting local stack (Docker volumes for db + files)…"
docker compose -f docker-compose.local.yml --env-file .env up --build -d
echo "✓ App:  http://localhost:8080   Login: admin / admin"
echo "  Logs: docker compose -f docker-compose.local.yml logs -f"
