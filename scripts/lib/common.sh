#!/usr/bin/env bash
# Общие функции для скриптов OmniSecretary.
# Подключается через: source "$(dirname "$0")/lib/common.sh"

# Корень репозитория (scripts/lib/common.sh → ../..).
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
COMPOSE_FILE="$ROOT_DIR/docker-compose.yml"
ENV_FILE="$ROOT_DIR/.env"

if [[ -t 1 ]]; then
    C_RESET=$'\033[0m'; C_INFO=$'\033[36m'; C_OK=$'\033[32m'
    C_WARN=$'\033[33m'; C_ERR=$'\033[31m'; C_BOLD=$'\033[1m'
else
    C_RESET=""; C_INFO=""; C_OK=""; C_WARN=""; C_ERR=""; C_BOLD=""
fi

log_info() { printf '%s[info]%s %s\n' "$C_INFO" "$C_RESET" "$*"; }
log_ok()   { printf '%s[ ok ]%s %s\n' "$C_OK"   "$C_RESET" "$*"; }
log_warn() { printf '%s[warn]%s %s\n' "$C_WARN" "$C_RESET" "$*" >&2; }
log_err()  { printf '%s[err ]%s %s\n'  "$C_ERR"  "$C_RESET" "$*" >&2; }
die()      { log_err "$*"; exit 1; }

# Выполнить команду или только показать её (DRY_RUN=1).
run() {
    if [[ "${DRY_RUN:-0}" == "1" ]]; then
        printf '  %s[dry-run]%s %s\n' "$C_WARN" "$C_RESET" "$*"
        return 0
    fi
    "$@"
}

# Интерактивное подтверждение. ASSUME_YES=1 — согласиться на всё.
confirm() {
    local prompt="$1"
    if [[ "${ASSUME_YES:-0}" == "1" ]]; then
        log_info "$prompt → да (--yes)"
        return 0
    fi
    if [[ ! -t 0 ]]; then
        log_warn "$prompt → нет (неинтерактивный запуск без --yes)"
        return 1
    fi
    local answer
    read -r -p "$(printf '%s%s%s [y/N]: ' "$C_BOLD" "$prompt" "$C_RESET")" answer
    [[ "$answer" =~ ^([Yy]|[Yy][Ee][Ss]|[Дд]|[Дд][Аа])$ ]]
}

load_env() {
    [[ -f "$ENV_FILE" ]] || die "Не найден $ENV_FILE. Скопируйте .env.example в .env или запустите scripts/bootstrap-pi.sh"
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a
}

# Определяем, нужен ли sudo для docker (после установки группа docker
# применяется только в новой сессии).
detect_docker() {
    if docker info >/dev/null 2>&1; then
        DOCKER=(docker)
    elif sudo -n docker info >/dev/null 2>&1; then
        DOCKER=(sudo docker)
        log_warn "docker доступен только через sudo (перелогиньтесь, чтобы войти в группу docker)"
    else
        DOCKER=(docker)
    fi
}

compose() {
    [[ ${#DOCKER[@]} -gt 0 ]] || detect_docker
    "${DOCKER[@]}" compose \
        --project-directory "$ROOT_DIR" \
        -f "$COMPOSE_FILE" \
        --env-file "$ENV_FILE" \
        "$@"
}

gen_hex() { openssl rand -hex "${1:-24}"; }

# Заменить значение переменной в .env (или добавить, если её нет).
env_set() {
    local key="$1" value="$2" file="${3:-$ENV_FILE}"
    if grep -qE "^${key}=" "$file"; then
        # | как разделитель — значения могут содержать / и :
        sed -i "s|^${key}=.*|${key}=${value}|" "$file"
    else
        printf '%s=%s\n' "$key" "$value" >> "$file"
    fi
}

env_get() {
    local key="$1" file="${2:-$ENV_FILE}"
    grep -E "^${key}=" "$file" 2>/dev/null | head -1 | cut -d= -f2-
}
