#!/usr/bin/env bash
# Delete all timesheet_records (Review / filed records).
# Keeps inbox, pipeline, employees.
#
#   bash scripts/db/delete-records.sh
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
echo "▶ Clearing timesheet records…"
"$SCRIPT_DIR/_compose.sh" clear_records
echo "✓ Timesheet records cleared"
