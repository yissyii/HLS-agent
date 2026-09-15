#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
export PYTHONDONTWRITEBYTECODE=1
exec "${PYTHON:-python3}" -B "$SCRIPT_DIR/serve/paired_entry.py" "$@"
