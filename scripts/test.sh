#!/usr/bin/env bash
# Run the backend end-to-end test suite (no Docker / Redis required).
set -euo pipefail
cd "$(dirname "$0")/../backend"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q -r requirements.txt
.venv/bin/python -m pytest "$@"
