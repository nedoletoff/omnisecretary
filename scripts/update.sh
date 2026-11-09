#!/usr/bin/env bash
#
# Безопасное обновление стека.
# Версии зафиксированы в docker-compose.yml — «обновление» означает
# осознанную правку тегов там, а затем запуск этого скрипта.
#
# Что делает:
#   1. Фиксирует текущие образы (для отката).
#   2. Загружает образы по тегам из compose.
#   3. Перезапускает изменённые сервисы.
#   4. Прогоняет smoke-тест.
#
# Использование: ./scripts/update.sh [--yes]
#
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

[[ "${1:-}" == "--yes" ]] && ASSUME_YES=1

load_env
detect_docker

STAMP="$(date +%Y%m%d-%H%M%S)"
SNAP_DIR="${DATA_ROOT}/backups/images-$STAMP"
mkdir -p "$SNAP_DIR"

log_info "Текущие образы → $SNAP_DIR/images.txt"
compose images > "$SNAP_DIR/images.txt" 2>/dev/null || true

log_info "Загрузка образов по фиксированным тегам…"
compose pull

confirm "Перезапустить стек с новыми образами?" || die "Отменено."

compose up -d --remove-orphans

log_info "Ожидание готовности…"
ok=0
for _ in $(seq 1 30); do
    if "$SCRIPT_DIR/smoke-test.sh" --quiet >/dev/null 2>&1; then ok=1; break; fi
    sleep 10
done

if [[ "$ok" == "1" ]]; then
    "$SCRIPT_DIR/smoke-test.sh"
    log_ok "Обновление успешно"
else
    log_err "Smoke-тест не прошёл после обновления."
    cat <<EOF

Откат:
  1. Верните теги в docker-compose.yml на предыдущие (см. $SNAP_DIR/images.txt).
  2. make pull && make up

Логи: make logs
EOF
    exit 1
fi
