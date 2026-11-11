#!/usr/bin/env bash
# Регистрация MCP-серверов в Open WebUI (идемпотентно).
set -Eeuo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$SCRIPT_DIR/init-mcp.py" "$@"
