# Архитектура

## Схема сервисов

```
                    LAN / Cloudflare Tunnel
                              │
                    :80 (HTTP) │ :443 (HTTPS)
                    ┌─────────▼──────────┐
                    │       nginx        │  reverse proxy
                    │  WS / SSE / ACME   │  mem_limit 32M
                    └─────────┬──────────┘
                              │ network: frontend
                    ┌─────────▼──────────┐
                    │     open-webui     │  ядро: чаты, RAG, календарь,
                    │   ghcr.io v0.13.3  │  пресеты, API, веб-сокеты
                    │  mem_limit 1280M   │
                    └──┬──────┬──────┬───┘
                       │      │      │   network: backend
             ┌─────────▼┐ ┌───▼────┐ ┌▼─────────┐
             │ postgres │ │ qdrant │ │  redis   │
             │ pg16+vec │ │ v1.20  │ │ 8.8      │
             │  384M    │ │  512M  │ │  64M     │
             └──────────┘ └────────┘ └──────────┘
```

## Сети

| Сеть | Кто в ней | Наружу |
|---|---|---|
| `frontend` | nginx, open-webui | nginx публикует 80/443 |
| `backend` | open-webui, postgres, qdrant, redis | нет (Qdrant — только 127.0.0.1:6333) |

nginx не имеет доступа к БД. Postgres/Qdrant/Redis не видны из LAN.

## Порты

| Порт | Сервис | Публикация |
|---|---|---|
| 80 | nginx | `0.0.0.0` |
| 443 | nginx | `0.0.0.0` |
| 6333 | Qdrant | `127.0.0.1` (snapshot API для бэкапов) |
| 5432 | PostgreSQL | только сеть `backend` |
| 6379 | Redis | только сеть `backend` |
| 8080 | Open WebUI | только сеть `frontend` |

## Тома

| Том | Путь на хосте | Содержимое | Бэкап |
|---|---|---|---|
| `postgres_data` | `${DATA_ROOT}/volumes/postgres` | чаты, пользователи, настройки | pg_dump |
| `qdrant_data` | `${DATA_ROOT}/volumes/qdrant` | векторы памяти | snapshot API |
| `qdrant_snapshots` | `${DATA_ROOT}/volumes/qdrant-snapshots` | временные снапшоты | — |
| `redis_data` | `${DATA_ROOT}/volumes/redis` | кэш/сессии | не нужен |
| `openwebui_data` | `${DATA_ROOT}/volumes/openwebui` | загрузки, конфиг | `--with-files` |
| — | `${DATA_ROOT}/backups` | бэкапы | — |
| — | `${DATA_ROOT}/vault` | Obsidian-вольт (git) | git push |
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

### Календарь и задачи (этап 2)
Open WebUI (встроенный календарь) ↔ CalDAV Radicale.
Vikunja ↔ PostgreSQL. MCP-сервер Vikunja (этап 4) даёт ассистенту
создавать задачи на естественном языке.

## Порядок запуска

1. `postgres` → healthy (`pg_isready`).
2. `redis` → healthy (`PING`).
3. `qdrant` → healthy (TCP 6333).
4. `open-webui` → ждёт postgres/redis/qdrant, миграции БД, `/health`.
5. `nginx` → стартует сразу, проксирует, когда ядро готово.

`restart: unless-stopped` — после перезагрузки Pi стек поднимается сам.

## Бюджет памяти (лимиты compose)

| Сервис | Лимит |
|---|---|
| nginx | 32 МБ |
| open-webui | 1280 МБ |
| postgres | 384 МБ |
| qdrant | 512 МБ |
| redis | 64 МБ |
| **Итого лимитов** | **2272 МБ** |

ОС + Docker ≈ 600–800 МБ. Запас ~700 МБ и swap 2 ГБ.
Лимиты — потолок, не резервация: в простое стек занимает заметно меньше.

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
