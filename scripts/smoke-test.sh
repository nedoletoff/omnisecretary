#!/usr/bin/env bash
#
# Smoke-тест стека: контейнеры, healthchecks, HTTP-эндпоинты, БД.
# Код возврата 0 — всё в порядке, иначе число провалившихся проверок.
#
# Использование: ./scripts/smoke-test.sh [--quiet]
#
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

QUIET=0
[[ "${1:-}" == "--quiet" ]] && QUIET=1

FAILED=0
PASSED=0

pass() { PASSED=$((PASSED + 1)); [[ "$QUIET" == "1" ]] || printf '  %s✔%s %s\n' "$C_OK" "$C_RESET" "$*"; }
fail() { FAILED=$((FAILED + 1)); [[ "$QUIET" == "1" ]] || printf '  %s✘%s %s\n' "$C_ERR" "$C_RESET" "$*"; }

load_env
detect_docker

# ── 1. Состояние контейнеров ─────────────────────────────────
if [[ "$QUIET" == "0" ]]; then
    printf '\n%sКонтейнеры%s\n' "$C_BOLD" "$C_RESET"
fi

ps_json="$(compose ps --format json 2>/dev/null || true)"
if [[ -z "$ps_json" ]]; then
    fail "docker compose ps не вернул данные (стек не запущен?)"
else
    # Compose v2 отдаёт либо массив, либо по одному JSON на строку.
    while IFS='|' read -r svc state health exitcode; do
        [[ -z "$svc" ]] && continue
        # Одноразовые сервисы (db-init) должны успешно завершиться, а не работать.
        if [[ "$svc" == "db-init" ]]; then
            if [[ "$state" == "exited" && "$exitcode" == "0" ]]; then
                pass "$svc: завершился успешно"
            else
                fail "$svc: $state (код $exitcode)"
            fi
            continue
        fi
        if [[ "$state" == "running" && ( "$health" == "healthy" || -z "$health" ) ]]; then
            pass "$svc: $state${health:+ / $health}"
        else
            fail "$svc: $state${health:+ / $health}"
        fi
    done < <(printf '%s' "$ps_json" | python3 -c '
import json, sys
raw = sys.stdin.read().strip()
items = []
try:
    data = json.loads(raw)
    items = data if isinstance(data, list) else [data]
except json.JSONDecodeError:
    for line in raw.splitlines():
        line = line.strip()
        if line:
            items.append(json.loads(line))
for it in items:
    print("|".join([it.get("Service",""), it.get("State",""), it.get("Health","") or "", str(it.get("ExitCode", 0))]))
')
fi

# ── 2. HTTP через nginx ──────────────────────────────────────
if [[ "$QUIET" == "0" ]]; then
    printf '\n%sHTTP%s\n' "$C_BOLD" "$C_RESET"
fi

check_http() {
    local name="$1" url="$2" expect="${3:-200}" code
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$url" 2>/dev/null || echo 000)"
    if [[ "$code" == "$expect" ]]; then
        pass "$name → HTTP $code"
    else
        fail "$name → HTTP $code (ожидался $expect) [$url]"
    fi
}

check_http "nginx /healthz"  "http://127.0.0.1/healthz" "200"
check_http "open-webui /health (через nginx)" "http://127.0.0.1/health" "200"

# ── 2b. Задачи (Vikunja) ─────────────────────────────────────
if [[ "$QUIET" == "0" ]]; then
    printf '\n%sЗадачи (Vikunja)%s\n' "$C_BOLD" "$C_RESET"
fi
check_http "vikunja /healthz (nginx :8081)" "http://127.0.0.1:8081/healthz" "200"
check_http "vikunja /api/v1/info" "http://127.0.0.1:8081/api/v1/info" "200"

# Календарь Open WebUI: эндпоинт существует и требует авторизации.
check_http "open-webui /api/v1/calendars/ (без токена)" "http://127.0.0.1/api/v1/calendars/" "401"

# ── 3. Qdrant ────────────────────────────────────────────────
if [[ "$QUIET" == "0" ]]; then
    printf '\n%sQdrant%s\n' "$C_BOLD" "$C_RESET"
fi
qdrant_body="$(curl -s --max-time 10 -H "api-key: ${QDRANT_API_KEY}" \
    "http://127.0.0.1:6333/healthz" 2>/dev/null || true)"
if [[ "$qdrant_body" == *"healthz check passed"* ]]; then
    pass "qdrant /healthz"
else
    fail "qdrant /healthz (ответ: ${qdrant_body:-пусто})"
fi

# ── 4. PostgreSQL ────────────────────────────────────────────
if [[ "$QUIET" == "0" ]]; then
    printf '\n%sБазы данных%s\n' "$C_BOLD" "$C_RESET"
fi
if compose exec -T postgres pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB" >/dev/null 2>&1; then
    pass "postgres pg_isready"
else
    fail "postgres pg_isready"
fi

if compose exec -T postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc \
    "select 1 from information_schema.tables where table_name='chat' limit 1" >/dev/null 2>&1; then
    pass "postgres: схема Open WebUI инициализирована"
else
    fail "postgres: схема Open WebUI ещё не создана (нормально при первом старте)"
fi

if compose exec -T postgres psql -U "$POSTGRES_USER" -d vikunja -tAc \
    "select 1 from information_schema.tables where table_name='tasks' limit 1" >/dev/null 2>&1; then
    pass "postgres: схема Vikunja инициализирована"
else
    fail "postgres: база vikunja пуста (запустите: make up)"
fi

# ── 4b. Модели и пресеты (если есть API-ключ владельца) ──────
API_KEY_FILE="$DATA_ROOT/secrets/webui-api-key.txt"
if [[ -f "$API_KEY_FILE" ]]; then
    if [[ "$QUIET" == "0" ]]; then
        printf '\n%sМодели%s\n' "$C_BOLD" "$C_RESET"
    fi
    api_key="$(grep -m1 '^WEBUI_API_KEY=' "$API_KEY_FILE" | cut -d= -f2-)"
    models_json="$(curl -fsS -m 30 -H "Authorization: Bearer $api_key" \
        http://127.0.0.1/api/models 2>/dev/null || true)"
    if printf '%s' "$models_json" | python3 -c '
import json, sys
data = json.load(sys.stdin)
ids = {m.get("id") for m in data.get("data", [])}
required = {"openai-fast", "secretary", "omni", "archivist", "coder", "private"}
missing = required - ids
sys.exit(1 if missing else 0)
' 2>/dev/null; then
        pass "модели: openai-fast и 5 пресетов на месте"
    else
        fail "модели: не хватает базовой модели или пресетов (make init-webui)"
    fi
fi

# ── 4c. MCP-инструменты и вольт ─────────────────────────────
if [[ "$QUIET" == "0" ]]; then
    printf '\n%sMCP и вольт%s\n' "$C_BOLD" "$C_RESET"
fi
check_http "vault-manager /healthz" "http://127.0.0.1/vault/healthz" "200"
check_http "vault-manager /vault/ (без авторизации)" "http://127.0.0.1/vault/" "401"

if [[ -f "$API_KEY_FILE" ]]; then
    api_key="$(grep -m1 '^WEBUI_API_KEY=' "$API_KEY_FILE" | cut -d= -f2-)"
    tools_json="$(curl -fsS -m 30 -H "Authorization: Bearer $api_key" \
        http://127.0.0.1/api/v1/tools/ 2>/dev/null || true)"
    if printf '%s' "$tools_json" | python3 -c '
import json, sys
data = json.load(sys.stdin)
ids = {t.get("id") for t in data} if isinstance(data, list) else set()
required = {f"server:mcp:{n}" for n in ("vault", "files", "github", "vikunja", "sandbox")}
missing = required - ids
if missing:
    print("нет: " + ", ".join(sorted(missing)))
sys.exit(1 if missing else 0)
' 2>/dev/null; then
        pass "MCP: 5 серверов зарегистрированы"
    else
        fail "MCP: серверы не зарегистрированы (make mcp-init)"
    fi
fi

# ── 5. Redis ─────────────────────────────────────────────────
if [[ "$(compose exec -T redis redis-cli ping 2>/dev/null | tr -d '\r')" == "PONG" ]]; then
    pass "redis PING"
else
    fail "redis PING"
fi

# ── Итог ─────────────────────────────────────────────────────
if [[ "$QUIET" == "0" ]]; then
    printf '\n%sИтог:%s %d ок, %d ошибок\n\n' "$C_BOLD" "$C_RESET" "$PASSED" "$FAILED"
fi
exit "$FAILED"
