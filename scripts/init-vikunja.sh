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
    exit 0
fi

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

# Примечание: флаг instance-admin в Vikunja 2.x — платная функция
# (admin-panel license). Для одно пользовательской установки он не нужен:
# проекты и задачи создаёт обычный пользователь. Пользователей добавляет CLI.

log_ok "Пользователь Vikunja создан: $EMAIL"
echo
log_info "Вход: ${VIKUNJA_PUBLIC_URL:-http://127.0.0.1:8081/}"
if [[ -z "${VIKUNJA_ADMIN_PASSWORD:-}" ]]; then
    log_warn "Пароль лежит в $PASSWORD_FILE — смените его в интерфейсе и удалите файл."
fi
