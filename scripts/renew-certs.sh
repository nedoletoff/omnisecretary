#!/usr/bin/env bash
# Продление сертификатов Let's Encrypt без rootful Docker.
#
# Сертификаты живут в ${DATA_ROOT}/letsencrypt (владелец — пользователь),
# поэтому rootless-демон может их читать и писать. После продления копия
# кладётся в nginx/certs (см. sync-certs.sh) — её читает контейнер nginx.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "${ROOT_DIR}/scripts/lib/common.sh"

load_env
detect_docker

LE_ROOT="${DATA_ROOT}/letsencrypt"
WEBROOT="${ROOT_DIR}/nginx/www"

[[ -d "$LE_ROOT/live" ]] || { log_err "нет ${LE_ROOT}/live"; exit 1; }

"${DOCKER[@]}" run --rm \
    -v "${LE_ROOT}:/etc/letsencrypt" \
    -v "${WEBROOT}:/var/www/certbot" \
    certbot/certbot:v5.10.0 renew \
        --webroot -w /var/www/certbot \
        --quiet --no-random-sleep-on-renew

"${ROOT_DIR}/scripts/sync-certs.sh" >/dev/null

if "${DOCKER[@]}" ps --format '{{.Names}}' | grep -qx omni-nginx; then
    "${DOCKER[@]}" exec omni-nginx nginx -s reload
    log_ok "nginx перезагружен с новыми сертификатами"
fi
