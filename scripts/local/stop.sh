#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
echo "▶ Stopping local stack…"
docker compose -f docker-compose.local.yml down
echo "✓ Stopped. (add --volumes to also wipe db + files + redis)"
