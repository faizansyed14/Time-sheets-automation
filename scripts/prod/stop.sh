#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
echo "▶ Stopping prod stack…"
docker compose -f docker-compose.prod.yml down
echo "✓ Stopped. (Postgres/S3 are external — unaffected. add --volumes to also drop the Redis broker volume, losing only queued in-flight jobs.)"
