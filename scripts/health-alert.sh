#!/usr/bin/env bash
# Мониторинг: прогоняет smoke-тест и шлёт алерт в Telegram при ошибках.
# Запускать по таймеру (пример unit — docs/operations.md).
# Секреты читаются из .env, в вывод не попадают.
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ -f .env ]]; then
    set -a
    # shellcheck disable=SC1091
    . ./.env
    set +a
fi

LOG_DIR="${DATA_ROOT:-/srv/omnisecretary}/logs"
mkdir -p "$LOG_DIR" 2>/dev/null || LOG_DIR="/tmp"
LOG_FILE="$LOG_DIR/health-alert.log"

output="$(./scripts/smoke-test.sh --quiet 2>&1)"
status=$?
printf '%s\n%s\n' "$(date -Is)" "$output" >> "$LOG_FILE"

if [[ $status -eq 0 ]]; then
    exit 0
fi

if [[ -z "${TELEGRAM_BOT_TOKEN:-}" || -z "${TELEGRAM_OWNER_ID:-}" ]]; then
    echo "Smoke-тест упал, но Telegram не настроен. Подробности: $LOG_FILE" >&2
    exit "$status"
fi

# Обрезаем длинный вывод: Telegram не примет больше 4096 символов.
message="$(printf '⚠️ OmniSecretary: smoke-тест упал\n\n%s' "$output" | tail -c 3500)"

curl -fsS --max-time 20 \
    -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
    --data-urlencode "chat_id=${TELEGRAM_OWNER_ID}" \
    --data-urlencode "text=${message}" \
    --data-urlencode "disable_web_page_preview=true" \
    -o /dev/null || echo "Не удалось отправить алерт в Telegram" >&2

exit "$status"
