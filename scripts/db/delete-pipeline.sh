#!/usr/bin/env bash
# Delete all pipeline_files rows (Activity log / staged items).
# Keeps emails, records, employees.
#
# Does NOT delete raw retry copies on disk/S3 — there is no age-based purge any
# more; a copy is removed when its item is Accepted or deleted (DELETE /pipeline/{id}).
#
#   bash scripts/db/delete-pipeline.sh
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
echo "▶ Clearing pipeline…"
"$SCRIPT_DIR/_compose.sh" clear_pipeline
echo "✓ Pipeline cleared"
