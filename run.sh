#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
export PYTHONDONTWRITEBYTECODE=1
export PYTHONUTF8=1
cd "$SCRIPT_DIR"
exec "${PYTHON:-python3}" -B -m agent.interface.entry "$@"
