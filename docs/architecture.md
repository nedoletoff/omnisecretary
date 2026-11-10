# Архитектура

## Схема сервисов

```
                    LAN / Cloudflare Tunnel
                              │
                 :80 / :443   │   :8081 (только LAN)
                    ┌─────────▼──────────┐
                    │       nginx        │  reverse proxy
                    │  WS / SSE / ACME   │  mem_limit 32M
                    └────┬───────────┬───┘
                         │ frontend  │
                    ┌────▼───────┐ ┌─▼──────────┐
                    │ open-webui │ │  vikunja   │  задачи, CalDAV
                    │  v0.13.3   │ │  v2.8.0    │  mem_limit 192M
                    │  1280M     │ └─────┬──────┘
                    └──┬──────┬──┘       │ network: backend
                       │      │          │
          ┌────────────▼┐ ┌───▼────┐ ┌───▼──────┐
          │  postgres   │ │ qdrant │ │  redis   │
          │  pg16+vec   │ │ v1.19  │ │  8.8     │
          │    384M     │ │  512M  │ │   64M    │
          └─────────────┘ └────────┘ └──────────┘
```

## Сети

| Сеть | Кто в ней | Наружу |
|---|---|---|
| `frontend` | nginx, open-webui, vikunja | nginx публикует 80/443/8081 |
| `backend` | open-webui, vikunja, postgres, qdrant, redis, db-init | нет (Qdrant — только 127.0.0.1:6333) |

nginx не имеет доступа к БД. Postgres/Qdrant/Redis не видны из LAN.
Vikunja стоит в обеих сетях: nginx проксирует её через `frontend`,
а к своей базе она ходит через `backend`.

## Порты

| Порт | Сервис | Публикация |
|---|---|---|
| 80 | nginx | `0.0.0.0` |
| 443 | nginx | `0.0.0.0` |
| 8081 | nginx → Vikunja | `0.0.0.0` (только LAN, наружу не пробрасывать) |
| 6333 | Qdrant | `127.0.0.1` (snapshot API для бэкапов) |
| 5432 | PostgreSQL | только сеть `backend` |
| 6379 | Redis | только сеть `backend` |
| 8080 | Open WebUI | только сеть `frontend` |
| 3456 | Vikunja | только внутри Docker-сетей |
| — | Telegram-бот | исходящий long polling, входящих портов нет |

## Тома

| Том | Путь на хосте | Содержимое | Бэкап |
|---|---|---|---|
| `postgres_data` | `${DATA_ROOT}/volumes/postgres` | чаты, пользователи, настройки | pg_dump |
| `qdrant_data` | `${DATA_ROOT}/volumes/qdrant` | векторы памяти | snapshot API |
| `qdrant_snapshots` | `${DATA_ROOT}/volumes/qdrant-snapshots` | временные снапшоты | — |
| `redis_data` | `${DATA_ROOT}/volumes/redis` | кэш/сессии | не нужен |
| `openwebui_data` | `${DATA_ROOT}/volumes/openwebui` | загрузки, конфиг | `--with-files` |
| `vikunja_data` | `${DATA_ROOT}/volumes/vikunja` | вложения задач | `--with-files` |
| — | `${DATA_ROOT}/backups` | бэкапы | — |
| — | `${DATA_ROOT}/vault` | Obsidian-вольт (git) | git push |
| — | `${DATA_ROOT}/secrets` | пароли, созданные скриптами (600) | не бэкапить в git |
| — | `${DATA_ROOT}/docker` | docker data-root | — |

`${DATA_ROOT}` по умолчанию:
- внешний диск: `/mnt/data/omnisecretary`;
- без внешнего диска: `/srv/omnisecretary` (SD-карта).

## Потоки данных

### Чат из веба
Браузер → nginx (:80/:443) → open-webui:8080 → PostgreSQL (история) →
облачный LLM API (ответ) → nginx (SSE-стрим) → браузер.

### Память (RAG)
Файл/сообщение → open-webui → облачный embedding API → вектор → Qdrant.
Запрос → embedding → поиск в Qdrant → контекст в промпт → LLM.

### Telegram (этап 3)
Telegram → bot (long polling) → API open-webui (`/api/chat/completions`
с `chat_id`) → та же запись в PostgreSQL, что и веб.

### Календарь и задачи
- Календарь: Open WebUI → PostgreSQL (события, календари, гранты доступа).
  CalDAV в Open WebUI v0.13.3 нет — см. ADR-016.
- Задачи: браузер → nginx (:8081) → Vikunja → PostgreSQL (`vikunja`).
  Vikunja отдаёт задачи по CalDAV (`/dav/`) для календарей на телефоне.
- MCP-сервер Vikunja (этап 4) даёт ассистенту создавать задачи на
  естественном языке.

## Порядок запуска

1. `postgres` → healthy (`pg_isready`).
2. `redis` → healthy (`PING`).
3. `qdrant` → healthy (TCP 6333).
4. `db-init` → создаёт БД `vikunja` и завершается с кодом 0.
5. `open-webui` → ждёт postgres/redis/qdrant, миграции БД, `/health`.
6. `vikunja` → ждёт postgres и успешный `db-init`, прогоняет свои миграции.
7. `nginx` → стартует сразу, проксирует, когда ядро готово.

`restart: unless-stopped` — после перезагрузки Pi стек поднимается сам.

## Бюджет памяти (лимиты compose)

| Сервис | Лимит |
|---|---|
| nginx | 32 МБ |
| open-webui | 1280 МБ |
| postgres | 384 МБ |
| qdrant | 512 МБ |
| redis | 64 МБ |
| vikunja | 192 МБ |
| db-init | 128 МБ (одноразово) |
| **Итого лимитов** | **2464 МБ** |

ОС + Docker ≈ 600–800 МБ. Запас ~500 МБ и swap 2 ГБ.
Лимиты — потолок, не резервация: в простое стек занимает заметно меньше.
На практике после старта занято ~1,9 ГБ (см. `free -h`).

## Внешний доступ

Вариант A — только LAN: `http://192.168.0.133`, без TLS (данные в своей сети).

Вариант B — Cloudflare Tunnel (cloudflared уже установлен на хосте):
`cloudflared tunnel --url http://127.0.0.1:80` или именованный туннель с доменом.
Порты наружу не открываются, TLS терминируется в Cloudflare.

Вариант C — свой домен + Let's Encrypt: см. `nginx/conf.d/10-ssl.conf.example`,
webroot уже смонтирован в `/var/www/certbot`.

## Перенос на внешний диск

Когда появится HDD/SSD:

```bash
# 1. Смонтировать диск в /mnt/data (см. docs/decisions.md, ADR-011)
# 2. Остановить стек
make down
# 3. Скопировать данные
sudo rsync -aHAX /srv/omnisecretary/ /mnt/data/omnisecretary/
# 4. Переключить data-root и DATA_ROOT
#    /etc/docker/daemon.json → "data-root": "/mnt/data/omnisecretary/docker"
#    .env → DATA_ROOT=/mnt/data/omnisecretary
sudo systemctl restart docker
# 5. Поднять и проверить
make up && make smoke
```

Docker-образы при смене data-root придётся скачать заново (`make pull`) —
это ожидаемо и безопасно.
