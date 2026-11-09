#!/usr/bin/env bash
# Обёртка для импорта пресетов (см. scripts/import-presets.py --help).
set -Eeuo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$SCRIPT_DIR/import-presets.py" "$@"
