#!/usr/bin/env bash
# Interactive: pick LOCAL / DEV / PROD and write root .env from .env.example.
#
#   ./scripts/create-env.sh
#   ./scripts/create-env.sh LOCAL     # non-interactive
#   ./scripts/create-env.sh DEV --force
set -euo pipefail
cd "$(dirname "$0")/.."

PROFILE="${1:-}"
FORCE="${2:-}"
EXAMPLE=".env.example"
OUT=".env"

if [[ -z "$PROFILE" ]]; then
  echo "Which environment profile should .env use?"
  echo "  1) LOCAL  — docker-compose.local.yml  (Postgres + disk in Docker)"
  echo "  2) DEV    — docker-compose.dev.yml    (AWS RDS + S3)"
  echo "  3) PROD   — docker-compose.prod.yml   (production; CHANGE_ME secrets)"
  echo
  read -r -p "Enter 1, 2, or 3 (or LOCAL/DEV/PROD): " choice
  case "${choice^^}" in
    1|LOCAL) PROFILE=LOCAL ;;
    2|DEV)   PROFILE=DEV ;;
    3|PROD)  PROFILE=PROD ;;
    *)
      echo "✗ Invalid choice: $choice" >&2
      exit 1
      ;;
  esac
fi

PROFILE="${PROFILE^^}"
if [[ ! "$PROFILE" =~ ^(LOCAL|DEV|PROD)$ ]]; then
  echo "Usage: $0 [LOCAL|DEV|PROD] [--force]" >&2
  exit 1
fi

if [[ ! -f "$EXAMPLE" ]]; then
  echo "✗ Missing $EXAMPLE" >&2
  exit 1
fi

if [[ -f "$OUT" && "$FORCE" != "--force" ]]; then
  echo "⚠ .env already exists."
  read -r -p "Overwrite with PROFILE: $PROFILE? [y/N] " ans
  case "${ans,,}" in
    y|yes) FORCE=--force ;;
    *)
      echo "Cancelled — left existing .env alone."
      exit 0
      ;;
  esac
fi

# Grab the PROFILE: XXX block (commented KEY=value lines) and uncomment them.
TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT

awk -v profile="$PROFILE" '
  BEGIN { in_block=0 }
  $0 ~ "^# =+$" { next }
  $0 ~ ("^# PROFILE: " profile "[[:space:]]") { in_block=1; next }
  in_block && $0 ~ /^# PROFILE:/ { exit }
  in_block && $0 ~ /^# =+$/ { exit }
  in_block {
    if ($0 ~ /^# [A-Za-z_][A-Za-z0-9_]*=/) {
      sub(/^# /, "")
      print
    }
  }
' "$EXAMPLE" > "$TMP"

if [[ ! -s "$TMP" ]]; then
  echo "✗ No KEY=value lines found for PROFILE: $PROFILE in .env.example" >&2
  exit 1
fi

{
  echo "# Auto-generated from .env.example — PROFILE: $PROFILE"
  echo "# Edit secrets here. Re-run: ./scripts/create-env.sh $PROFILE --force"
  echo "#"
  cat "$TMP"
} > "$OUT"

echo "✓ Wrote .env from PROFILE: $PROFILE ($(grep -c '^[A-Za-z_]' "$OUT" || true) keys)"
echo
case "$PROFILE" in
  LOCAL)
    echo "Next:  ./scripts/local/start.sh"
    echo "App →  http://localhost:8080   (admin / admin)"
    ;;
  DEV)
    echo "Edit .env: DATABASE_URL (RDS), S3_BUCKET, JWT_SECRET, API keys, Graph secrets."
    echo "Next:  ./scripts/dev/start.sh"
    ;;
  PROD)
    echo "Edit .env: replace every CHANGE_ME."
    echo "Next:  ./scripts/prod/start.sh"
    ;;
esac
