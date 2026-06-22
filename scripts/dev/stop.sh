#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
echo "▶ Stopping dev stack…"
docker compose -f docker-compose.dev.yml down
echo "✓ Stopped. (add --volumes to also drop data)"
