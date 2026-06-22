#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
echo "▶ Stopping prod stack…"
docker compose -f docker-compose.prod.yml down
echo "✓ Stopped. (add --volumes to also drop the database — irreversible)"
