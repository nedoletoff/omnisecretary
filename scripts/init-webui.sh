#!/usr/bin/env bash
# Первичная настройка Open WebUI (владелец, API-ключ, LLM, пресеты).
set -Eeuo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$SCRIPT_DIR/init-webui.py" "$@"
