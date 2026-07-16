#!/usr/bin/env bash
# Reset inbox workflow: ingested / archived / extracted / no-sheets → new.
# Deletes email-sourced pipeline staging for those messages. Keeps records,
# employees, and vault files.
#
#   bash scripts/db/reset-inbox.sh
#   bash scripts/db/reset-inbox.sh --dry-run
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$ROOT"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.local.yml}"
if [ ! -f "$COMPOSE_FILE" ]; then
  COMPOSE_FILE="docker-compose.dev.yml"
fi
echo "▶ Resetting inbox status…"
# Seed modules are baked into the image at build time — sync this file so the
# script works without a full rebuild after adding/changing it locally.
docker compose -f "$COMPOSE_FILE" cp \
  "$ROOT/backend/app/seed/reset_inbox_status.py" \
  "backend:/app/app/seed/reset_inbox_status.py"
docker compose -f "$COMPOSE_FILE" exec -T backend python -m app.seed.reset_inbox_status "$@"
echo "✓ Inbox reset"
