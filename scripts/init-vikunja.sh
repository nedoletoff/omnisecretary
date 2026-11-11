#!/usr/bin/env bash
#
# Создаёт администратора Vikunja (задачи).
#
# Vikunja не имеет публичной регистрации (VIKUNJA_SERVICE_ENABLEREGISTRATION=false),
# поэтому первый пользователь создаётся через CLI внутри контейнера.
# Скрипт идемпотентен: если пользователь уже есть, ничего не делает.
#
# Пароль:
#   - из переменной VIKUNJA_ADMIN_PASSWORD (если задана), либо
#   - генерируется случайно и сохраняется в ${DATA_ROOT}/secrets/vikunja-admin.txt
#     (права 600). Прочитайте, смените пароль в интерфейсе, файл удалите.
#
# Использование:
#   ./scripts/init-vikunja.sh
#
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

load_env
detect_docker

USERNAME="${VIKUNJA_ADMIN_USERNAME:-admin}"
EMAIL="${VIKUNJA_ADMIN_EMAIL:-admin@omnisecretary.local}"
VIKUNJA_BIN="/app/vikunja/vikunja"

log_info "Проверяю контейнер Vikunja…"
if ! compose ps --status running --services 2>/dev/null | grep -qx vikunja; then
    die "Контейнер vikunja не запущен. Сначала: make up"
fi

if compose exec -T vikunja "$VIKUNJA_BIN" user list 2>/dev/null | grep -q "$USERNAME"; then
    log_ok "Пользователь $USERNAME уже существует"
else
    SECRETS_DIR="$DATA_ROOT/secrets"
    PASSWORD_FILE="$SECRETS_DIR/vikunja-admin.txt"

    if [[ -n "${VIKUNJA_ADMIN_PASSWORD:-}" ]]; then
        password="$VIKUNJA_ADMIN_PASSWORD"
        log_info "Пароль взят из VIKUNJA_ADMIN_PASSWORD"
    else
        password="$(gen_hex 16)"
        if [[ ! -d "$SECRETS_DIR" ]]; then
            run sudo mkdir -p "$SECRETS_DIR"
            run sudo chown "$(id -u):$(id -g)" "$SECRETS_DIR"
        fi
        umask 077
        printf '%s\n' "$password" > "$PASSWORD_FILE"
        chmod 600 "$PASSWORD_FILE"
        log_ok "Пароль сохранён в $PASSWORD_FILE (права 600)"
    fi

    log_info "Создаю пользователя $USERNAME…"
    compose exec -T vikunja "$VIKUNJA_BIN" user create \
        --username "$USERNAME" --email "$EMAIL" --password "$password" >/dev/null
fi

SECRETS_DIR="${SECRETS_DIR:-$DATA_ROOT/secrets}"
PASSWORD_FILE="${PASSWORD_FILE:-$SECRETS_DIR/vikunja-admin.txt}"

# Примечание: флаг instance-admin в Vikunja 2.x — платная функция
# (admin-panel license). Для одно пользовательской установки он не нужен:
# проекты и задачи создаёт обычный пользователь. Пользователей добавляет CLI.

log_ok "Пользователь Vikunja готов: $EMAIL"

# ── API-токен для MCP ────────────────────────────────────────
# Токен нужен mcp-vikunja, чтобы создавать и закрывать задачи.
# Vikunja показывает токен один раз — сохраняем в secrets и .env.
ensure_api_token() {
    local token_file="$SECRETS_DIR/vikunja-api-token.txt"
    if [[ -f "$token_file" ]] && grep -q '^VIKUNJA_API_TOKEN=..*' "$ENV_FILE" 2>/dev/null; then
        log_ok "API-токен Vikunja уже создан"
        return 0
    fi

    local password=""
    if [[ -n "${VIKUNJA_ADMIN_PASSWORD:-}" ]]; then
        password="$VIKUNJA_ADMIN_PASSWORD"
    elif [[ -f "$PASSWORD_FILE" ]]; then
        password="$(cat "$PASSWORD_FILE")"
    else
        log_warn "Нет пароля Vikunja — создайте API-токен вручную (Настройки → API-токены)"
        return 1
    fi

    local base="${VIKUNJA_PUBLIC_URL:-http://127.0.0.1:8081/}"
    base="${base%/}"
    local jwt
    jwt="$(curl -fsS -X POST "$base/api/v1/login" -H "Content-Type: application/json" \
        -d "{\"username\":\"$USERNAME\",\"password\":\"$password\"}" \
        | python3 -c 'import json,sys; print(json.load(sys.stdin).get("token",""))')" || true
    if [[ -z "$jwt" ]]; then
        log_warn "Не удалось войти в Vikunja для выпуска API-токена"
        return 1
    fi

    local response
    response="$(curl -fsS -X PUT "$base/api/v1/tokens" \
        -H "Authorization: Bearer $jwt" -H "Content-Type: application/json" \
        -d '{"title":"omnisecretary-mcp","expires_at":"2030-01-01T00:00:00Z","permissions":{"projects":["read_all"],"tasks":["read_all","create","update"],"labels":["read_all"]}}')" || true
    local api_token
    api_token="$(printf '%s' "$response" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("token",""))' 2>/dev/null)" || true
    if [[ -z "$api_token" ]]; then
        log_warn "Vikunja не выдала API-токен (возможно, уже существует)"
        return 1
    fi

    umask 077
    printf '%s\n' "$api_token" > "$token_file"
    chmod 600 "$token_file"
    env_set VIKUNJA_API_TOKEN "$api_token"
    log_ok "API-токен Vikunja сохранён в secrets и .env"
    log_info "После этого: make restart (mcp-vikunja подхватит токен)"
}

ensure_api_token || true
echo
log_info "Вход: ${VIKUNJA_PUBLIC_URL:-http://127.0.0.1:8081/}"
if [[ -z "${VIKUNJA_ADMIN_PASSWORD:-}" ]]; then
    log_warn "Пароль лежит в $PASSWORD_FILE — смените его в интерфейсе и удалите файл."
fi
