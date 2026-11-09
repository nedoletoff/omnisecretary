#!/usr/bin/env bash
#
# Одноразовая инициализация баз данных в контейнере postgres.
# Запускается сервисом db-init из docker-compose.yml.
#
# Создаёт отдельную БД для Vikunja, чтобы не смешивать данные Open WebUI
# и задач. Идемпотентно: повторный запуск ничего не меняет.
set -Eeuo pipefail

# Список дополнительных БД (пробел как разделитель).
DATABASES="${EXTRA_DATABASES:-vikunja}"

for db in $DATABASES; do
    exists="$(psql -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='${db}'")"
    if [[ "$exists" == "1" ]]; then
        echo "[db-init] база ${db} уже существует"
    else
        echo "[db-init] создаю базу ${db}"
        psql -d postgres -c "CREATE DATABASE \"${db}\" OWNER \"${PGUSER}\""
    fi
done

echo "[db-init] готово"
