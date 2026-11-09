#!/usr/bin/env bash
#
# Включает memory-cgroup на Raspberry Pi.
#
# Без него Docker игнорирует mem_limit («memory limit capabilities ...
# discarded»), и один контейнер может съесть всю память Pi.
#
# Что делает:
#   1. Проверяет, включён ли контроллер memory.
#   2. Добавляет cgroup_enable=memory cgroup_memory=1 в /boot/firmware/cmdline.txt
#      (с резервной копией). Идемпотентно.
#   3. Просит перезагрузку (или выполняет с --reboot).
#
# Использование:
#   ./scripts/enable-memory-cgroup.sh            # только правка, ребут вручную
#   ./scripts/enable-memory-cgroup.sh --reboot   # правка + перезагрузка
#
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

CMDLINE="/boot/firmware/cmdline.txt"
PARAMS="cgroup_enable=memory cgroup_memory=1"
REBOOT=0
[[ "${1:-}" == "--reboot" ]] && REBOOT=1

if grep -qw memory /sys/fs/cgroup/cgroup.controllers 2>/dev/null; then
    log_ok "memory-cgroup уже включён — ничего делать не нужно"
    exit 0
fi

[[ -f "$CMDLINE" ]] || die "Не найден $CMDLINE (это не Raspberry Pi OS?)"

if grep -q "cgroup_enable=memory" "$CMDLINE"; then
    log_warn "Параметры уже прописаны, но контроллер не активен. Нужна перезагрузка."
else
    confirm "Добавить '$PARAMS' в $CMDLINE (потребуется перезагрузка)?" \
        || die "Отменено."
    sudo cp "$CMDLINE" "${CMDLINE}.bak.$(date +%Y%m%d%H%M%S)"
    # Файл — одна строка без перевода строки в конце.
    sudo sed -i "\$ s/\$/ $PARAMS/" "$CMDLINE"
    log_ok "Параметры добавлены (резервная копия рядом: ${CMDLINE}.bak.*)"
fi

echo
log_info "Текущая строка cmdline:"
sudo cat "$CMDLINE"
echo
log_warn "Изменения вступят в силу только после перезагрузки."

if [[ "$REBOOT" == "1" ]]; then
    confirm "Перезагрузить Pi сейчас?" || die "Перезагрузка отложена."
    sudo reboot
else
    log_info "Перезагрузите вручную: sudo reboot"
    log_info "После ребута проверьте: cat /sys/fs/cgroup/cgroup.controllers (должен быть memory)"
fi
