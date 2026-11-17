#!/usr/bin/env bash
# Копирует сертификаты Let's Encrypt в каталог, доступный rootless Docker.
# /etc/letsencrypt закрыт для обычного пользователя, а rootless-демон не
# может читать root-only файлы. Копия живёт в nginx/certs (в git не попадает).
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="${ROOT_DIR}/nginx/certs"
LIVE="/etc/letsencrypt/live"
TARGET_UID="${TARGET_UID:-$(id -u)}"
TARGET_GID="${TARGET_GID:-$(id -g)}"

# Читать /etc/letsencrypt может только root — берём sudo один раз.
if ! ls "$LIVE" >/dev/null 2>&1; then
    exec sudo env TARGET_UID="$TARGET_UID" TARGET_GID="$TARGET_GID" \
        bash "${BASH_SOURCE[0]}" "$@"
fi

shopt -s nullglob
for domain_dir in "$LIVE"/*/; do
    domain="$(basename "$domain_dir")"
    [[ -f "${domain_dir}fullchain.pem" ]] || continue
    install -d -m 700 -o "$TARGET_UID" -g "$TARGET_GID" "${DEST}/${domain}"
    install -m 600 -o "$TARGET_UID" -g "$TARGET_GID" \
        "${domain_dir}fullchain.pem" "${DEST}/${domain}/fullchain.pem"
    install -m 600 -o "$TARGET_UID" -g "$TARGET_GID" \
        "${domain_dir}privkey.pem" "${DEST}/${domain}/privkey.pem"
    echo "[ok] ${domain} → ${DEST}/${domain}"
done
chown -R "${TARGET_UID}:${TARGET_GID}" "$DEST"
