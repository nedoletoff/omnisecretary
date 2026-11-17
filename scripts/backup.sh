#!/usr/bin/env bash
#
# Бэкап OmniSecretary:
#   - pg_dump (custom format) базы Open WebUI;
#   - снапшоты всех коллекций Qdrant через snapshot API;
#   - git-синхронизация Obsidian-вольта (если он есть);
#   - опционально — файлы Open WebUI (--with-files).
#
# Использование:
#   ./scripts/backup.sh [--with-files] [--keep N]
#
set -Eeuo pipefail
# Дампы содержат всю переписку: доступ только владельцу.
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

WITH_FILES=0
KEEP=14

while [[ $# -gt 0 ]]; do
    case "$1" in
        --with-files) WITH_FILES=1 ;;
        --keep)       KEEP="${2:?нужно число}"; shift ;;
        -h|--help)    sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) die "Неизвестный аргумент: $1" ;;
    esac
    shift
done

load_env
detect_docker

BACKUP_ROOT="${DATA_ROOT}/backups"
TS="$(date +%Y%m%d-%H%M%S)"
DEST="$BACKUP_ROOT/$TS"

mkdir -p "$DEST"
log_info "Бэкап в $DEST"

# ── 1. PostgreSQL ────────────────────────────────────────────
log_info "pg_dump…"
compose exec -T postgres pg_dump \
    -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc \
    > "$DEST/postgres-${POSTGRES_DB}.dump"
[[ -s "$DEST/postgres-${POSTGRES_DB}.dump" ]] || die "pg_dump вернул пустой файл"
log_ok "PostgreSQL: $(du -h "$DEST/postgres-${POSTGRES_DB}.dump" | cut -f1)"

# ── 2. Qdrant ────────────────────────────────────────────────
log_info "Снапшоты Qdrant…"
collections="$(curl -fsS --max-time 15 -H "api-key: ${QDRANT_API_KEY}" \
    "http://127.0.0.1:6333/collections" \
    | python3 -c 'import json,sys; print("\n".join(c["name"] for c in json.load(sys.stdin)["result"]["collections"]))' \
    2>/dev/null || true)"

if [[ -z "$collections" ]]; then
    log_warn "Коллекции Qdrant не найдены (пустая память) — пропускаю"
else
    while read -r col; do
        [[ -z "$col" ]] && continue
        snap="$(curl -fsS --max-time 60 -X POST \
            -H "api-key: ${QDRANT_API_KEY}" \
            "http://127.0.0.1:6333/collections/${col}/snapshots" \
            | python3 -c 'import json,sys; print(json.load(sys.stdin)["result"]["name"])')"
        # Qdrant кладёт снапшот в подкаталог коллекции, а не в корень.
        src="$("${DOCKER[@]}" exec omni-qdrant sh -c "find /qdrant/snapshots -name '${snap}' -type f | head -1" | tr -d '\r')"
        [[ -n "$src" ]] || { log_err "Qdrant: снапшот ${snap} не найден"; continue; }
        "${DOCKER[@]}" cp "omni-qdrant:${src}" "$DEST/" >/dev/null
        curl -fsS -X DELETE -H "api-key: ${QDRANT_API_KEY}" \
            "http://127.0.0.1:6333/collections/${col}/snapshots/${snap}" >/dev/null || true
        log_ok "Qdrant: $col → $snap"
    done <<< "$collections"
fi

# ── 3. Obsidian-вольт ────────────────────────────────────────
VAULT_PATH="${VAULT_PATH:-${DATA_ROOT}/vault}"
# Токен из secrets — чтобы push работал без интерактивного ввода.
GIT_PUSH_ENV=()
VAULT_TOKEN_FILE="${DATA_ROOT}/secrets/vault-git-token"
if [[ -r "$VAULT_TOKEN_FILE" ]]; then
    VAULT_TOKEN="$(cat "$VAULT_TOKEN_FILE")"
    if [[ -n "$VAULT_TOKEN" ]]; then
        BASIC="$(printf 'x-access-token:%s' "$VAULT_TOKEN" | base64 -w0)"
        GIT_PUSH_ENV=(
            "GIT_CONFIG_COUNT=1"
            "GIT_CONFIG_KEY_0=http.extraheader"
            "GIT_CONFIG_VALUE_0=AUTHORIZATION: basic $BASIC"
        )
    fi
fi
unset VAULT_TOKEN BASIC

if [[ -d "$VAULT_PATH/.git" ]]; then
    log_info "Синхронизация Obsidian-вольта…"
    if [[ -n "$(git -C "$VAULT_PATH" status --porcelain)" ]]; then
        git -C "$VAULT_PATH" add -A
        git -C "$VAULT_PATH" commit -m "chore: автосохранение вольта $(date +%F)" >/dev/null
    fi
    if git -C "$VAULT_PATH" remote get-url origin >/dev/null 2>&1; then
        env "${GIT_PUSH_ENV[@]}" GIT_TERMINAL_PROMPT=0 \
            git -C "$VAULT_PATH" push --quiet 2>/dev/null \
            && log_ok "Вольт отправлен в origin" \
            || log_warn "Не удалось отправить вольт (нет сети/доступа). Локальный коммит сохранён."
    else
        log_warn "У вольта нет remote origin — только локальный коммит"
    fi
else
    log_info "Obsidian-вольт не найден ($VAULT_PATH) — пропускаю"
fi

# ── 4. Файлы Open WebUI (опционально) ────────────────────────
if [[ "$WITH_FILES" == "1" ]]; then
    log_info "Архив файлов Open WebUI…"
    tar -czf "$DEST/openwebui-data.tgz" \
        --exclude='./cache' --exclude='./vector_db' \
        -C "${DATA_ROOT}/volumes/openwebui" .
    log_ok "Open WebUI files: $(du -h "$DEST/openwebui-data.tgz" | cut -f1)"
fi

# ── 5. Контрольные суммы и ротация ───────────────────────────
( cd "$DEST" && sha256sum ./* > SHA256SUMS 2>/dev/null || true )
log_ok "Контрольные суммы записаны"

mapfile -t old < <(find "$BACKUP_ROOT" -maxdepth 1 -mindepth 1 -type d -printf '%f\n' | sort -r | tail -n +$((KEEP + 1)))
for d in "${old[@]:-}"; do
    [[ -z "$d" ]] && continue
    log_info "Удаляю старый бэкап $d"
    rm -rf "${BACKUP_ROOT:?}/$d"
done

log_ok "Готово: $DEST ($(du -sh "$DEST" | cut -f1))"
