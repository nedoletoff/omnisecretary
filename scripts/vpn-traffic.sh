#!/usr/bin/env bash
# Трафик VPN-прокси (sing-box). Считает байты, прошедшие через контейнер.
#
#   ./scripts/vpn-traffic.sh            # итоги с момента старта
#   ./scripts/vpn-traffic.sh --watch 5  # скорость каждые 5 секунд
#   ./scripts/vpn-traffic.sh --log      # добавить точку в журнал
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

CONTAINER="${VPN_CONTAINER:-omni-vpn}"
DATA_ROOT="$(grep -m1 '^DATA_ROOT=' .env 2>/dev/null | cut -d= -f2- | tr -d '"' || true)"
DATA_ROOT="${DATA_ROOT:-/srv/omnisecretary}"
LOG_FILE="$DATA_ROOT/logs/vpn-traffic.log"

die() { echo "ошибка: $*" >&2; exit 1; }

running() {
    sudo docker ps --format '{{.Names}}' | grep -qx "$CONTAINER"
}

counter() {
    # $1 = rx_bytes | tx_bytes
    sudo docker exec "$CONTAINER" cat "/sys/class/net/eth0/statistics/$1" 2>/dev/null || echo 0
}

human() {
    awk -v b="$1" 'BEGIN {
        split("Б КБ МБ ГБ ТБ", u, " "); i = 1;
        while (b >= 1024 && i < 5) { b /= 1024; i++ }
        printf "%.1f %s", b, u[i]
    }'
}

totals() {
    local rx tx
    rx="$(counter rx_bytes)"
    tx="$(counter tx_bytes)"
    echo "VPN-трафик ($CONTAINER): получено $(human "$rx"), отправлено $(human "$tx")"
}

log_point() {
    mkdir -p "$(dirname "$LOG_FILE")"
    echo "$(date -Is) $(counter rx_bytes) $(counter tx_bytes)" >> "$LOG_FILE"
    echo "записано: $LOG_FILE"
}

watch() {
    local interval="${1:-5}" prev_rx prev_tx rx tx now
    prev_rx="$(counter rx_bytes)"; prev_tx="$(counter tx_bytes)"
    echo "наблюдение каждые ${interval} с (Ctrl+C — выход)"
    while true; do
        sleep "$interval"
        rx="$(counter rx_bytes)"; tx="$(counter tx_bytes)"
        now="$(date +%H:%M:%S)"
        echo "$now ↓ $(human $(((rx - prev_rx) / interval)))/с  ↑ $(human $(((tx - prev_tx) / interval)))/с  (всего ↓ $(human "$rx"), ↑ $(human "$tx"))"
        prev_rx="$rx"; prev_tx="$tx"
    done
}

[[ "$(uname -s)" == Linux ]] || die "только Linux"

case "${1:-}" in
    --watch) watch "${2:-5}" ;;
    --log) running || die "контейнер $CONTAINER не запущен"; log_point ;;
    ""|--total) running || die "контейнер $CONTAINER не запущен"; totals ;;
    *) die "неизвестный аргумент: $1" ;;
esac
