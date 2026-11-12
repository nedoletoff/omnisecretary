#!/usr/bin/env bash
# Продление сертификатов Let's Encrypt и перезагрузка nginx.
# Запускать по cron/systemd-timer (пример в docs/router.md).
# Требует sudo для docker и чтения /etc/letsencrypt.
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo "[1/2] certbot renew"
sudo docker run --rm \
    -v /etc/letsencrypt:/etc/letsencrypt \
    -v /var/lib/letsencrypt:/var/lib/letsencrypt \
    -v "$ROOT_DIR/nginx/www:/var/www/certbot" \
    certbot/certbot renew --webroot -w /var/www/certbot --quiet

echo "[2/2] reload nginx"
if sudo docker ps --format '{{.Names}}' | grep -qx omni-nginx; then
    sudo docker exec omni-nginx nginx -s reload
    echo "nginx перезагружен"
else
    echo "контейнер omni-nginx не запущен — пропускаю reload"
fi
