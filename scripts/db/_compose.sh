#!/usr/bin/env bash
# Run a backend seed module inside Docker.
# Usage: _compose.sh clear_pipeline | clear_records
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
MODULE="${1:?seed module required (e.g. clear_pipeline)}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.local.yml}"
if [ ! -f "$COMPOSE_FILE" ]; then
  COMPOSE_FILE="docker-compose.dev.yml"
fi
exec docker compose -f "$COMPOSE_FILE" exec -T backend python -m "app.seed.${MODULE}"
