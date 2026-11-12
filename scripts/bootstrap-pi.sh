#!/usr/bin/env bash
#
# Идемпотентная подготовка Raspberry Pi 4 для OmniSecretary.
#
# Что делает:
#   1. Проверяет платформу (aarch64, Debian/Raspberry Pi OS).
#   2. Выбирает каталог данных (внешний диск /mnt/data или SD-карта).
#   3. Устанавливает Docker Engine + Compose v2 (если ещё нет).
#   4. Переносит docker data-root на диск данных (логи, live-restore).
#   5. Создаёт каталоги томов и бэкапов.
#   6. Генерирует .env с секретами (не перезаписывает существующий).
#   7. Отключает нативный nginx, если он занимает порт 80.
#   8. Поднимает стек и прогоняет smoke-тест.
#
# Использование:
#   ./scripts/bootstrap-pi.sh                 # интерактивно, с подтверждениями
#   ./scripts/bootstrap-pi.sh --yes           # без вопросов (осознанно!)
#   ./scripts/bootstrap-pi.sh --dry-run       # только показать команды
#   ./scripts/bootstrap-pi.sh --data-root /mnt/data/omnisecretary
#   ./scripts/bootstrap-pi.sh --skip-docker   # Docker уже установлен
#
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

trap 'log_err "Ошибка в строке $LINENO. Прерываю."' ERR

ASSUME_YES=0
DRY_RUN=0
SKIP_DOCKER=0
DATA_ROOT_OVERRIDE=""
DOCKER_GROUP_ADDED=0

usage() {
    sed -n '2,28p' "$0" | sed 's/^# \{0,1\}//'
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --yes|-y)        ASSUME_YES=1 ;;
        --dry-run)       DRY_RUN=1 ;;
        --skip-docker)   SKIP_DOCKER=1 ;;
        --data-root)     DATA_ROOT_OVERRIDE="${2:?нужен путь}"; shift ;;
        -h|--help)       usage ;;
        *) die "Неизвестный аргумент: $1 (см. --help)" ;;
    esac
    shift
done

# ── 0. Проверка платформы ────────────────────────────────────
preflight() {
    log_info "Проверка платформы…"
    local arch
    arch="$(uname -m)"
    [[ "$arch" == "aarch64" ]] || log_warn "Архитектура $arch, ожидалась aarch64. Продолжаю на свой риск."
    [[ -f "$ROOT_DIR/.env.example" ]] || die "Нет $ROOT_DIR/.env.example — запустите скрипт из репозитория."
    command -v curl >/dev/null || die "Нужен curl: sudo apt-get install -y curl"
    command -v openssl >/dev/null || die "Нужен openssl: sudo apt-get install -y openssl"
    if [[ -r /etc/os-release ]]; then
        # shellcheck disable=SC1091
        . /etc/os-release
        log_info "ОС: ${PRETTY_NAME:-unknown}"
    fi
    log_ok "Платформа в порядке ($arch)"
}

# ── 1. Каталог данных ────────────────────────────────────────
choose_data_root() {
    if [[ -n "$DATA_ROOT_OVERRIDE" ]]; then
        DATA_ROOT="$DATA_ROOT_OVERRIDE"
    elif [[ -d /mnt/data && -w /mnt/data ]]; then
        DATA_ROOT="/mnt/data/omnisecretary"
    else
        DATA_ROOT="/srv/omnisecretary"
        log_warn "Внешний диск /mnt/data не найден — данные будут на SD-карте ($DATA_ROOT)."
        log_warn "Позже перенос: см. docs/architecture.md (раздел «Перенос на внешний диск»)."
    fi
    log_info "Каталог данных: $DATA_ROOT"
    confirm "Использовать $DATA_ROOT для данных и docker data-root?" \
        || die "Отменено пользователем."
}

# ── 2. Docker ────────────────────────────────────────────────
install_docker() {
    if [[ "$SKIP_DOCKER" == "1" ]]; then
        log_info "Установка Docker пропущена (--skip-docker)."
        return 0
    fi
    if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
        log_ok "Docker и Compose уже установлены: $(docker --version)"
        return 0
    fi

    log_info "Установка Docker Engine из официального репозитория…"
    confirm "Установить Docker (apt: docker-ce, containerd, compose-plugin)?" \
        || die "Отменено пользователем."

    local codename
    codename="$(. /etc/os-release && echo "${VERSION_CODENAME:-trixie}")"

    run sudo install -m 0755 -d /etc/apt/keyrings
    run sudo curl -fsSL https://download.docker.com/linux/debian/gpg \
        -o /etc/apt/keyrings/docker.asc
    run sudo chmod a+r /etc/apt/keyrings/docker.asc
    if [[ "$DRY_RUN" != "1" ]]; then
        printf 'deb [arch=arm64 signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian %s stable\n' "$codename" \
            | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
    else
        log_info "[dry-run] запись /etc/apt/sources.list.d/docker.list ($codename stable)"
    fi
    run sudo apt-get update
    if ! run sudo apt-get install -y \
        docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin; then
        log_warn "Официальный репозиторий не сработал — пробую установщик get.docker.com"
        run bash -c 'curl -fsSL https://get.docker.com | sudo sh'
    fi

    if [[ "$DRY_RUN" != "1" ]] && ! docker compose version >/dev/null 2>&1; then
        die "Docker Compose v2 не установился. Проверьте apt-репозиторий Docker для $codename."
    fi
    if [[ "$DRY_RUN" == "1" ]]; then
        log_info "[dry-run] Docker был бы установлен"
    else
        log_ok "Docker установлен"
    fi
}

setup_docker_group() {
    if id -nG "$USER" | grep -qw docker; then
        return 0
    fi
    log_info "Добавляю пользователя $USER в группу docker…"
    run sudo usermod -aG docker "$USER"
    DOCKER_GROUP_ADDED=1
}

# ── 3. daemon.json / data-root ───────────────────────────────
configure_docker_daemon() {
    local daemon_json="/etc/docker/daemon.json"
    local desired
    desired="$(cat <<JSON
{
  "data-root": "$DATA_ROOT/docker",
  "log-driver": "json-file",
  "log-opts": { "max-size": "10m", "max-file": "3" },
  "live-restore": true,
  "features": { "buildkit": true }
}
JSON
)"

    if [[ -f "$daemon_json" ]] && [[ "$(sudo cat "$daemon_json")" == "$desired" ]]; then
        log_ok "daemon.json уже настроен (data-root: $DATA_ROOT/docker)"
        return 0
    fi

    if [[ -d /var/lib/docker ]] && [[ -n "$(sudo ls -A /var/lib/docker 2>/dev/null)" ]]; then
        log_warn "В /var/lib/docker уже есть данные: смена data-root сделает текущие образы/контейнеры невидимыми."
        confirm "Всё равно переключить data-root на $DATA_ROOT/docker?" || return 0
    fi

    confirm "Настроить /etc/docker/daemon.json (data-root, лимиты логов)?" \
        || die "Отменено пользователем."

    if [[ "$DRY_RUN" == "1" ]]; then
        log_info "[dry-run] запись $daemon_json:"
        printf '%s\n' "$desired"
        return 0
    fi

    run sudo mkdir -p /etc/docker
    if [[ -f "$daemon_json" ]]; then
        run sudo cp "$daemon_json" "${daemon_json}.bak.$(date +%Y%m%d%H%M%S)"
    fi
    printf '%s\n' "$desired" | sudo tee "$daemon_json" >/dev/null
    run sudo systemctl restart docker
    log_ok "data-root: $DATA_ROOT/docker"
}

# ── 4. Каталоги ──────────────────────────────────────────────
create_directories() {
    log_info "Создание каталогов в $DATA_ROOT…"
    run sudo mkdir -p \
        "$DATA_ROOT"/docker \
        "$DATA_ROOT"/volumes/postgres \
        "$DATA_ROOT"/volumes/qdrant \
        "$DATA_ROOT"/volumes/qdrant-snapshots \
        "$DATA_ROOT"/volumes/redis \
        "$DATA_ROOT"/volumes/openwebui \
        "$DATA_ROOT"/volumes/vikunja \
        "$DATA_ROOT"/volumes/proactive \
        "$DATA_ROOT"/backups \
        "$DATA_ROOT"/vault \
        "$DATA_ROOT"/vault-index \
        "$DATA_ROOT"/shared \
        "$DATA_ROOT"/sandbox \
        "$DATA_ROOT"/bot \
        "$DATA_ROOT"/secrets \
        "$DATA_ROOT"/vpn \
        "$DATA_ROOT"/logs

    # Владельцы под UID внутри образов (иначе контейнер не запишет данные).
    run sudo chown -R 999:999 "$DATA_ROOT"/volumes/postgres "$DATA_ROOT"/volumes/redis
    run sudo chown -R 1000:1000 "$DATA_ROOT"/volumes/qdrant "$DATA_ROOT"/volumes/qdrant-snapshots "$DATA_ROOT"/volumes/openwebui "$DATA_ROOT"/volumes/vikunja
    # Остальное — текущему пользователю (бэкапы, вольт, логи).
    run sudo chown -R "$(id -u):$(id -g)" "$DATA_ROOT"/backups "$DATA_ROOT"/vault \
        "$DATA_ROOT"/vault-index "$DATA_ROOT"/shared "$DATA_ROOT"/sandbox \
        "$DATA_ROOT"/bot "$DATA_ROOT"/secrets "$DATA_ROOT"/logs
    # VPN-конфиг содержит ключи — только владельцу.
    run sudo chown -R "$(id -u):$(id -g)" "$DATA_ROOT"/vpn
    run sudo chmod 700 "$DATA_ROOT"/vpn

    run mkdir -p "$ROOT_DIR/nginx/certs" "$ROOT_DIR/nginx/www"
    log_ok "Каталоги готовы"
}

# ── 5. .env ──────────────────────────────────────────────────
setup_env() {
    if [[ -f "$ENV_FILE" ]]; then
        log_ok ".env уже существует — секреты не трогаю"
        # Мягко синхронизируем не-секретные значения и досоздаём
        # секреты, появившиеся в новых версиях (миграция .env).
        if [[ "$DRY_RUN" != "1" ]]; then
            env_set DATA_ROOT "$DATA_ROOT"
            if ! grep -q '^VIKUNJA_SECRET=..*' "$ENV_FILE"; then
                env_set VIKUNJA_SECRET "$(gen_hex 32)"
                log_info "Добавлен VIKUNJA_SECRET в .env"
            fi
            if ! grep -q '^VIKUNJA_PUBLIC_URL=..*' "$ENV_FILE"; then
                local ip_existing
                ip_existing="$(hostname -I 2>/dev/null | awk '{print $1}')"
                [[ -n "$ip_existing" ]] || ip_existing="127.0.0.1"
                env_set VIKUNJA_PUBLIC_URL "http://${ip_existing}:8081/"
            fi
            if ! grep -q '^VAULT_ADMIN_PASSWORD=..*' "$ENV_FILE"; then
                env_set VAULT_ADMIN_PASSWORD "$(gen_hex 16)"
                log_info "Добавлен VAULT_ADMIN_PASSWORD в .env"
            fi
        fi
        return 0
    fi

    log_info "Генерация .env с новыми секретами…"
    if [[ "$DRY_RUN" == "1" ]]; then
        log_info "[dry-run] cp .env.example .env + генерация секретов"
        return 0
    fi

    cp "$ROOT_DIR/.env.example" "$ENV_FILE"
    chmod 600 "$ENV_FILE"

    local ip
    ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
    [[ -n "$ip" ]] || ip="127.0.0.1"

    env_set DATA_ROOT "$DATA_ROOT"
    env_set WEBUI_URL "http://${ip}"
    env_set POSTGRES_PASSWORD "$(gen_hex 24)"
    env_set WEBUI_SECRET_KEY "$(gen_hex 32)"
    env_set QDRANT_API_KEY "$(gen_hex 24)"
    env_set VIKUNJA_SECRET "$(gen_hex 32)"
    env_set VIKUNJA_PUBLIC_URL "http://${ip}:8081/"
    env_set VAULT_ADMIN_PASSWORD "$(gen_hex 16)"

    local host_tz="UTC"
    [[ -f /etc/timezone ]] && host_tz="$(cat /etc/timezone)"
    env_set TZ "$host_tz"

    log_ok ".env создан (права 600). Ключи LLM нужно вписать вручную."
}

# ── 6. Конфликт с нативным nginx ─────────────────────────────
handle_host_nginx() {
    if ! systemctl list-unit-files nginx.service >/dev/null 2>&1; then
        return 0
    fi
    if ! systemctl is-active --quiet nginx; then
        return 0
    fi
    log_warn "Нативный nginx занимает порт 80 — контейнерный nginx не сможет стартовать."
    if confirm "Остановить и отключить нативный nginx (systemctl disable --now nginx)?"; then
        run sudo systemctl disable --now nginx
        if [[ "$DRY_RUN" == "1" ]]; then
            log_info "[dry-run] нативный nginx был бы отключён"
        else
            log_ok "Нативный nginx отключён (конфиги в /etc/nginx сохранены)"
        fi
    else
        log_warn "Нативный nginx оставлен. Контейнерный nginx упадёт: освободите порт 80 вручную."
    fi
}

# ── 7. Запуск стека ──────────────────────────────────────────
start_stack() {
    load_env
    detect_docker

    log_info "Загрузка образов (фиксированные теги)…"
    run compose pull
    log_info "Запуск стека…"
    run compose up -d --remove-orphans
    log_ok "Стек запущен"
}

wait_healthy() {
    local timeout="${1:-420}" waited=0
    log_info "Ожидание готовности сервисов (до ${timeout}s)…"
    while (( waited < timeout )); do
        if "$SCRIPT_DIR/smoke-test.sh" --quiet >/dev/null 2>&1; then
            log_ok "Все сервисы готовы"
            return 0
        fi
        sleep 10
        waited=$((waited + 10))
        printf '.'
    done
    printf '\n'
    log_warn "Сервисы не вышли в healthy за ${timeout}s. Диагностика: make logs"
    return 1
}

summary() {
    local ip
    ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
    cat <<EOF

${C_BOLD}Готово.${C_RESET}

  Веб-интерфейс:  http://${ip:-<ip>}
  Данные:         $DATA_ROOT
  Docker root:    $DATA_ROOT/docker

Дальше (ручные шаги):
  1. Откройте веб-интерфейс и создайте первого пользователя — он станет администратором.
  2. Впишите OPENAI_API_BASE_URL / OPENAI_API_KEY / EMBEDDING_* в .env и выполните: make restart
  3. Проверьте: make smoke
  4. Смените пароль администратора, проверьте, что регистрация закрыта (Settings → Admin).

Если добавляли пользователя в группу docker — перелогиньтесь или выполните: newgrp docker

EOF
    if [[ "$DOCKER_GROUP_ADDED" == "1" ]]; then
        log_warn "Пользователь добавлен в группу docker: нужен повторный вход."
    fi
}

main() {
    log_info "OmniSecretary bootstrap (DRY_RUN=$DRY_RUN, ASSUME_YES=$ASSUME_YES)"
    preflight
    choose_data_root
    install_docker
    setup_docker_group
    configure_docker_daemon
    create_directories
    setup_env
    handle_host_nginx
    if [[ "$DRY_RUN" == "1" ]]; then
        log_info "DRY_RUN: дальше были бы pull/up и smoke-тест."
        summary
        return 0
    fi
    start_stack
    wait_healthy || true
    "$SCRIPT_DIR/smoke-test.sh" || true
    summary
}

main "$@"
