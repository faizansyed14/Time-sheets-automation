#!/usr/bin/env bash
# Start the full DEV stack (backend + worker + redis + frontend) via Docker.
set -euo pipefail
cd "$(dirname "$0")/../.."
if [ ! -f .env ]; then
  echo "✗ No .env file. Copy the dev example and edit:" >&2
  echo "    cp .env.dev .env" >&2
  exit 1
fi
echo "▶ Starting dev stack (Docker)…"
docker compose -f docker-compose.dev.yml --env-file .env up --build -d
echo "✓ Backend:  http://localhost:8000  (docs: /docs)"
echo "✓ Frontend: http://localhost:5173"
echo "  Logs: docker compose -f docker-compose.dev.yml logs -f"
