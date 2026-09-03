#!/usr/bin/env bash
# Run the backend end-to-end test suite.
#
# Tests run against PostgreSQL (the only supported DB). Point TEST_DATABASE_URL
# at any reachable Postgres + a throwaway database; tables are dropped/recreated
# each run. Quickest way to get one:
#
#   docker compose -f docker-compose.dev.yml --env-file .env up -d db
#   createdb -h localhost -U timesheet timesheet_test    # once
#
set -euo pipefail
cd "$(dirname "$0")/../backend"

export TEST_DATABASE_URL="${TEST_DATABASE_URL:-postgresql+asyncpg://timesheet:timesheet@localhost:5432/timesheet_test}"
echo "▶ Tests against: $TEST_DATABASE_URL"

[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q -r requirements.txt
.venv/bin/python -m pytest "$@"
