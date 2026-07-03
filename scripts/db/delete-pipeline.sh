#!/usr/bin/env bash
# Delete all pipeline_files (Activity log / staged items).
# Keeps emails, records, employees.
#
#   bash scripts/db/delete-pipeline.sh
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
echo "▶ Clearing pipeline…"
"$SCRIPT_DIR/_compose.sh" clear_pipeline
echo "✓ Pipeline cleared"
