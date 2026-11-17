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
| `frontend` | nginx, open-webui, vikunja, vault-manager, portal | nginx публикует 80/443/8081 |
| `backend` | open-webui, vikunja, postgres, qdrant, redis, db-init, mcp-sandbox, vpn, llm-gateway, portal | нет (Qdrant — только 127.0.0.1:6333, VPN-прокси — 127.0.0.1:2080) |
| `mcp` | open-webui, vikunja, mcp-vault, mcp-files, mcp-github, mcp-vikunja | нет. Песочницы здесь нет — изоляция от токенов |

nginx не имеет доступа к БД. Postgres/Qdrant/Redis не видны из LAN.
Vikunja стоит в трёх сетях: nginx проксирует её через `frontend`, к своей
базе она ходит через `backend`, а MCP-сервер задач — через `mcp`.

## Порты

| Порт | Сервис | Публикация |
|---|---|---|
| 80 | nginx | `0.0.0.0` |
| 443 | nginx | `0.0.0.0` |
| 8081 | nginx → Vikunja | `0.0.0.0` (только LAN, наружу не пробрасывать) |
| 6333 | Qdrant | `127.0.0.1` (snapshot API для бэкапов) |
| 2080/2081 | VPN-прокси Xray | `127.0.0.1` (HTTP CONNECT / SOCKS5) |
| 8000 | portal (регистрация/настройки/календарь) | нет, только через nginx `/portal/` |
| 8088 | llm-gateway (OpenRouter → бесплатная) | `127.0.0.1` (для smoke) |
| 5432 | PostgreSQL | только сеть `backend` |
| 6379 | Redis | только сеть `backend`, требуется пароль |
| 8080 | Open WebUI | только сеть `frontend` |
| 3456 | Vikunja | только внутри Docker-сетей |
| — | Telegram-бот | исходящий long polling, входящих портов нет |
| — | MCP-серверы | только сеть `mcp` |
| — | vault-manager | через nginx по пути `/vault/`, Basic Auth |

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
| — | `${DATA_ROOT}/vault-index` | FTS5-индекс вольта | не нужен (пересоздаётся) |
| — | `${DATA_ROOT}/shared` | общий каталог файлов (MCP files) | `--with-files` не покрывает |
| — | `${DATA_ROOT}/sandbox` | песочница MCP (код, прототипы) | не нужен |
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
- MCP-сервер Vikunja даёт ассистенту создавать задачи на естественном языке.

### Проактивность
`proactive` раз в сутки в `BRIEFING_TIME` собирает погоду (api.met.no),
события (Open WebUI API), задачи (Vikunja API) и заметки (вольт, read-only)
и отправляет текст в Telegram владельцу. Состояние — том `proactive_state`.

### Голос
Браузер (микрофон) → open-webui → `stt:8000/v1/audio/transcriptions` →
sherpa-onnx (модель Vosk small-ru) → текст в чат. Ответ → open-webui →
`tts:8000/v1/audio/speech` → Silero v4_ru → WAV в браузер.
Оба сервиса — профиль `voice`, портов не публикуют.

### Инструменты (MCP)
LLM решает вызвать инструмент → open-webui (`/mcp`, streamable HTTP) →
контейнер `mcp-*` → результат возвращается в контекст → ответ LLM.
Вольт: `vault_search` (FTS5) → `vault_read` → при записи git-коммит и push.
Песочница: код исполняется в отдельном контейнере без доступа к хосту.

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
| mcp-vault | 128 МБ |
| mcp-files | 64 МБ |
| mcp-github | 96 МБ |
| mcp-vikunja | 64 МБ |
| mcp-sandbox | 192 МБ |
| proactive (профиль proactive) | 96 МБ |
| stt (профиль voice) | 320 МБ |
| tts Silero (профиль voice) | 640 МБ |
| tts-piper (профиль voice-piper) | 192 МБ |
| vault-manager | 96 МБ |
| telegram-bot (профиль) | 96 МБ |
| **Итого лимитов** | **3112 МБ** (без голоса) / **4072 МБ** (с Silero) |

ОС + Docker ≈ 600–800 МБ. Запас ~500 МБ и swap 2 ГБ.
Лимиты — потолок, не резервация: в простое стек занимает заметно меньше.
На практике после старта занято ~1,9 ГБ (см. `free -h`).

## Внешний доступ

Вариант A — только LAN: `http://192.168.0.102`, без TLS (данные в своей сети).

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
