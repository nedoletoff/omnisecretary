#!/usr/bin/env bash
#
# Усиление хоста Raspberry Pi: firewall и автообновления безопасности.
#
# Что делает:
#   1. ставит ufw и unattended-upgrades (apt);
#   2. разрешает входящие только 22, 80, 443 и 8081 из локальной сети;
#   3. включает автоматические обновления безопасности;
#   4. проверяет настройки SSH (не меняет их).
#
# Скрипт идемпотентен: повторный запуск ничего не ломает.
# Опасные шаги спрашивают подтверждение (y/N), флаг --yes отключает вопросы.
#
# Использование:
#   sudo ./scripts/harden-host.sh [--yes]
#
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

ASSUME_YES=0
[[ "${1:-}" == "--yes" ]] && ASSUME_YES=1

LAN_CIDR="${LAN_CIDR:-192.168.0.0/24}"

require_root() {
    [[ $EUID -eq 0 ]] || die "Запускать через sudo: sudo $0"
}

confirm() {
    local question="$1"
    if [[ $ASSUME_YES -eq 1 ]]; then
        log_info "$question — да (--yes)"
        return 0
    fi
    read -r -p "$question [y/N] " answer
    [[ "$answer" == "y" || "$answer" == "Y" ]]
}

# ── 1. Пакеты ────────────────────────────────────────────────
install_packages() {
    local missing=()
    dpkg -s ufw >/dev/null 2>&1 || missing+=(ufw)
    dpkg -s unattended-upgrades >/dev/null 2>&1 || missing+=(unattended-upgrades)
    if [[ ${#missing[@]} -eq 0 ]]; then
        log_ok "ufw и unattended-upgrades уже установлены"
        return 0
    fi
    log_info "Будут установлены: ${missing[*]}"
    if confirm "Установить пакеты через apt?"; then
        apt-get update -qq
        DEBIAN_FRONTEND=noninteractive apt-get install -y "${missing[@]}"
        log_ok "пакеты установлены"
    fi
}

# ── 2. Firewall ──────────────────────────────────────────────
configure_ufw() {
    command -v ufw >/dev/null 2>&1 || { log_warn "ufw не установлен — пропуск"; return 0; }

    log_info "Правила: 22, 80, 443 — всем; 8081 — только $LAN_CIDR"
    if ! confirm "Настроить и включить ufw?"; then
        log_warn "firewall не изменён"
        return 0
    fi

    ufw --force reset >/dev/null
    ufw default deny incoming >/dev/null
    ufw default allow outgoing >/dev/null
    ufw allow 22/tcp comment 'ssh' >/dev/null
    ufw allow 80/tcp comment 'http' >/dev/null
    ufw allow 443/tcp comment 'https' >/dev/null
    # Vikunja: только из домашней сети, наружу порт не проброшен.
    ufw allow from "$LAN_CIDR" to any port 8081 proto tcp comment 'vikunja lan' >/dev/null
    ufw --force enable >/dev/null
    log_ok "ufw включён"
    ufw status numbered
}

# ── 3. Автообновления безопасности ───────────────────────────
configure_unattended() {
    dpkg -s unattended-upgrades >/dev/null 2>&1 || { log_warn "unattended-upgrades нет — пропуск"; return 0; }

    local cfg=/etc/apt/apt.conf.d/20auto-upgrades
    if [[ -f "$cfg" ]] && grep -q 'Unattended-Upgrade "1"' "$cfg"; then
        log_ok "автообновления уже включены"
    else
        if confirm "Включить автоматические обновления безопасности?"; then
            cat > "$cfg" <<'EOF'
// Обновления безопасности ставятся автоматически, без перезагрузки.
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
APT::Periodic::AutocleanInterval "7";
EOF
            systemctl enable --now unattended-upgrades >/dev/null 2>&1 || true
            log_ok "автообновления включены"
        fi
    fi
}

# ── 4. Cron: снимок состояния и продление сертификата ───────
install_cron() {
    local project_dir data_root
    project_dir="$(cd "$SCRIPT_DIR/.." && pwd)"
    data_root="${DATA_ROOT:-/srv/omnisecretary}"
    if ! confirm "Поставить cron: снимок состояния раз в минуту и продление сертификата?"; then
        return 0
    fi
    cat > /etc/cron.d/omnisecretary-status <<EOF
# Снимок состояния для админ-панели портала. Портал только читает файл.
* * * * * root DATA_ROOT=$data_root STATUS_FILE=$data_root/status/device.json /usr/bin/python3 $project_dir/scripts/device-status.py >/dev/null 2>&1
EOF
    chmod 644 /etc/cron.d/omnisecretary-status
    cat > /etc/cron.d/omnisecretary-renew <<EOF
# Продление сертификата Let's Encrypt и перезагрузка nginx.
17 3 * * * root /bin/bash $project_dir/scripts/renew-certs.sh >>/var/log/omnisecretary-renew.log 2>&1
EOF
    chmod 644 /etc/cron.d/omnisecretary-renew
    mkdir -p "$data_root/status"
    DATA_ROOT="$data_root" STATUS_FILE="$data_root/status/device.json" \
        python3 "$project_dir/scripts/device-status.py" || true
    log_ok "cron установлен, снимок состояния создан"
}

# ── 5. Проверка SSH (только чтение) ──────────────────────────
check_ssh() {
    log_info "Проверка SSH:"
    sshd -T 2>/dev/null | grep -E '^(permitrootlogin|passwordauthentication|pubkeyauthentication)' \
        || log_warn "не удалось прочитать конфиг sshd"
    log_info "Ожидается: passwordauthentication no, permitrootlogin without-password"
}

require_root
install_packages
configure_ufw
configure_unattended
install_cron
check_ssh
log_ok "Готово. Проверить: sudo ufw status; systemctl status unattended-upgrades"
