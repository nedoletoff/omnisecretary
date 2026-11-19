# Архитектура OmniSecretary

## Обзор

```
Пользователи: браузер / PWA, Telegram, Android-приложение
        │ HTTPS (443), WebSocket
        ▼
   nginx (reverse proxy, TLS)
        │
        ├─► open-webui:8080  (ядро: UI, API, чаты, память, RAG, календарь)
        │        │
        │        ├─ PostgreSQL (pgvector) — пользователи, чаты, настройки, заметки
        │        ├─ Qdrant — векторная память
        │        ├─ Redis — кэш/очереди
        │        ├─ llm-gateway (опц.) → облачный OpenAI-совместимый API
        │        ├─ stt / tts — голос (профили voice)
        │        └─ MCP-серверы (backend/mcp сети): vault, files, github, vikunja, sandbox
        │
        ├─► portal:8000  (регистрация, аккаунт, /api/m/*, статус, per-user vault)
        │        ├─ Open WebUI API (от имени пользователя)
        │        ├─ PostgreSQL / Vikunja API / bot.db
        │        └─ ${DATA_ROOT}/vaults/<user_id> (клоны и индексы)
        │
        ├─► vikunja (задачи, CalDAV) — порт 8081
        └─► /vault/ → vault-manager (общий вольт владельца, Basic Auth)

Исходящие: telegram-bot, proactive, llm-gateway — через VPN-прокси (xray)
при необходимости. ntfy (опц.) — push-уведомления.
```

## Компоненты

| Компонент | Роль | Данные |
|---|---|---|
| nginx | TLS, маршрутизация, WebSocket/SSE, статика | конфиг, сертификаты |
| open-webui | Единое ядро: чаты, модели/пресеты, RAG, календарь, заметки | `VOLUMES_ROOT/openwebui` |
| postgres | Основная БД (openwebui, vikunja) | `VOLUMES_ROOT/postgres` |
| qdrant | Векторное хранилище | `VOLUMES_ROOT/qdrant` |
| redis | Кэш/служебные данные | `VOLUMES_ROOT/redis` |
| vikunja | Задачи, проекты, CalDAV | БД в postgres, файлы в `VOLUMES_ROOT/vikunja` |
| portal | Регистрация по токену, вход, личные настройки, админ-статус, **мобильный API** `/api/m/*`, **персональные вольты** | `portal.db`, `vaults/<user_id>` |
| vault-manager | Веб-форма и синхронизация общего вольта владельца | `/srv/omnisecretary/vault`, индекс |
| mcp-vault | MCP-инструменты вольта (общий контекст) | общий вольт/индекс |
| mcp-files/github/vikunja/sandbox | Инструменты для ассистента | shared, workspace, токены |
| telegram-bot | Telegram-интерфейс, напоминания, доступ по коду | `bot.db` |
| proactive | Утренний брифинг, расписание | состояние в volumes |
| stt / tts | Распознавание и синтез речи (OpenAI-совместимые) | модели в образах/кэше |
| ntfy (опц.) | Self-hosted push | `VOLUMES_ROOT/ntfy` |
| vpn (xray) | Прокси для заблокированных/зарубежных сервисов | `vpn/config.json` |
| Android-клиент | Нативный UI, локальное хранение сессии/очередей, системные интеграции | устройство пользователя |

## Сети Docker

- **frontend:** nginx и сервисы, принимающие входящий трафик.
- **backend:** ядро, БД, кэши, портал, голос, MCP, VPN-клиенты.
- **mcp:** изоляция MCP-серверов (Open WebUI и MCP; песочница ограничена).

БД и Qdrant не публикуются наружу (кроме диагностического 127.0.0.1 для Qdrant
при необходимости). Rootless Docker: демон пользователя, `sudo` не требуется.

## Хранение данных (DATA_ROOT)

По умолчанию `/srv/omnisecretary` (переносимо на внешний диск):

```
${DATA_ROOT}/
  volumes/            bind-монты сервисов (postgres, qdrant, redis, openwebui, vikunja, ntfy, ...)
  rootless/           data-root и volumes rootless Docker (текущее размещение)
  portal/             portal.db
  bot/                bot.db, настройки брифинга
  vault/              общий вольт владельца (git)
  vault-index/        индекс общего вольта
  vaults/<user_id>/   персональные клоны вольтов пользователей
  vaults/<user_id>.index/
  secrets/            токены, пароли, ключи (600)
  backups/            дампы и снапшоты
  logs/               журналы
  vpn/                конфигурация прокси
```

## Потоки данных

- **Чат (веб/Telegram/Android):** клиент → nginx → open-webui → LLM API;
  история в PostgreSQL, память/эмбеддинги — Qdrant. Все клиенты видят одну
  базу в рамках аккаунта.
- **Календарь/задачи:** Open WebUI API и Vikunja API (через портал `/api/m/*`
  от имени пользователя).
- **Персональный вольт:** приложение → `/api/m/vault/*` → портал проверяет
  права по папкам → git-операции/индекс в изолированном каталоге пользователя
  → (опц.) push в GitHub.
- **Голос:** клиент → `/api/m/voice|speak` или Open WebUI audio → stt/tts.
- **Брифинг:** proactive собирает погоду/календарь/задачи/заметки и отправляет
  в Telegram (через VPN при необходимости).

## Мобильный клиент (Android)

- Нативное приложение (Compose), авторизация Bearer-токеном Open WebUI.
- Использует Open WebUI API (SSE чаты, заметки, календарь, память) и
  `/api/m/*` (сводка, голос, задачи, напоминания, персональный вольт).
- Локально: зашифрованная сессия, очереди (заметки/напоминания) при оффлайне,
  системные интеграции (виджет, плитка, share, интенты, биометрия).
- Сборка в GitHub Actions, раздача — APK/Releases.

Подробно: [android.md](android.md), [mobile-android.md](mobile-android.md).

## Безопасность и изоляция

- TLS, закрытая регистрация/allowlist, rootless Docker, лимиты памяти/CPU,
  разделение сетей, минимальные права MCP.
- Права доступа к папкам вольта — серверная проверка на каждое действие.
- Секреты вне git; PAT и токены шифруются/хранятся с правами 600.

Подробнее: [security.md](security.md), [decisions.md](decisions.md).
