# OmniSecretary

Self-hosted персональный AI-секретарь для Raspberry Pi 4 (4 ГБ ОЗУ).

Одна база для веб-интерфейса, Telegram-бота и Android-приложения: чаты,
настройки, память и инструменты живут в одном бэкенде. LLM — через облачные
OpenAI-совместимые API, тяжёлые вычисления не выполняются на Pi.

## Текущее состояние

- ✅ **Ядро:** Open WebUI + PostgreSQL (pgvector) + Qdrant + Redis + Nginx,
  rootless Docker, HTTPS (Let's Encrypt), бэкапы, smoke-тесты.
- ✅ **Функции:** календарь, задачи (Vikunja), поиск, голос (STT/TTS),
  Telegram-бот, брифинг, MCP-инструменты, портал (регистрация/вход/настройки).
- ✅ **Obsidian:** персональный вольт на пользователя (репозиторий + PAT +
  права по папкам, программно на сервере) в приложении и `/api/m/*`;
  серверный вольт владельца через `/vault/` и MCP.
- ✅ **Android-клиент:** нативное приложение (Compose), 0.1.0-beta,
  чаты/голос/календарь/задачи/заметки/напоминания/вольт, виджет/плитка,
  Material You, CI и релизы — см. [docs/android.md](docs/android.md).

## Быстрый старт (на Pi)

```bash
# 1. Подготовка: Docker rootless, каталоги, .env, секреты
./scripts/bootstrap-pi.sh

# 2. Вписать ключи LLM/провайдеров в .env
nano .env

# 3. Запуск стека
make up

# 4. Инициализация (пресеты, MCP, права)
make init-webui
make mcp-build && make mcp-init

# 5. Проверка
make smoke
```

Подробно: [docs/operations.md](docs/operations.md), [docs/plan.md](docs/plan.md).

## Первый вход и начало работы

### Веб-интерфейс
- URL: `https://nedoletoff.sknt.ru`
- Логин владельца: `nedoletoff`
- Пароль: `make owner-creds` или `/srv/omnisecretary/secrets/webui-owner.txt`

### Регистрация пользователей
- `https://nedoletoff.sknt.ru/register` (логин + пароль + токен приглашения)
- Токен: `.env` → `TELEGRAM_CONFIRM_CODE` или `secrets/telegram-confirm-code.txt`
- Настройки аккаунта: `/portal/account`

### Android-приложение
- Установка: [GitHub Releases](https://github.com/nedoletoff/omnisecretary/releases)
  (`android-v*`) или артефакт CI.
- Сервер: `https://nedoletoff.sknt.ru`, вход тем же аккаунтом.
- Функции, личный Obsidian-вольт, сборка: [docs/android.md](docs/android.md).

### Telegram-бот (опционально, сейчас отключён)
- **Не запускается по умолчанию** (`COMPOSE_PROFILES` без `telegram`) — заменён Android-приложением.
- Код и конфигурация сохранены (профиль `telegram`). Включение: добавить `telegram` в `COMPOSE_PROFILES` в `.env` и `make up`, либо `make tg-up`.
- Команды/доступ по коду при включении — [docs/telegram.md](docs/telegram.md).
- Брифинг/напоминания в приложении работают через портал/API; доставка в Telegram требует профилей `telegram`/`proactive` и доступного Telegram API.

### Календарь и задачи
- Календарь Open WebUI: в вебе и в приложении.
- Vikunja: `http://<pi>:8081` (локально), пароль — `secrets/vikunja-admin.txt`.
- Быстрый доступ: `https://nedoletoff.mooo.com` — [docs/quick-access.md](docs/quick-access.md).

### Obsidian
- Персональный вольт: настраивается пользователем в приложении (репозиторий,
  PAT, права по папкам) — [docs/obsidian.md](docs/obsidian.md).
- Серверный вольт владельца: `/vault/` (Basic Auth, настройка и статус) —
  см. [docs/obsidian.md](docs/obsidian.md).

### Голос и поиск
- Голос (STT/TTS): `make voice-up`, профили `voice`/`voice-piper` — [docs/voice.md](docs/voice.md).
- Веб-поиск: DuckDuckGo по умолчанию — [docs/search.md](docs/search.md).

### Бэкапы и обслуживание

```bash
make backup        # pg_dump + снапшот Qdrant + метаданные
make smoke         # проверка здоровья
make renew-certs   # продление TLS (cron настроен)
```

Подробно: [docs/operations.md](docs/operations.md), [docs/router.md](docs/router.md),
[docs/vpn.md](docs/vpn.md).

## Сервисы и порты

| Сервис | Назначение | Доступ |
|---|---|---|
| nginx | reverse proxy, TLS, WebSocket | 80, 443 |
| open-webui | ядро (UI, API, чаты) | внутренний |
| postgres (pgvector) | основная БД | внутренний |
| qdrant | векторная память | 127.0.0.1:6333 |
| redis | кэш/очереди | внутренний |
| vikunja | задачи, CalDAV | 8081 (LAN/UFW по политике) |
| portal | регистрация, аккаунт, `/api/m/*`, статус | внутренний (через nginx) |
| mcp-* | vault, files, github, vikunja, sandbox | внутренняя сеть |
| telegram-bot, proactive | бот и брифинг | исходящие (через VPN при настройке) |
| stt, tts | голос | внутренний |
| ntfy (опционально) | push-уведомления | 127.0.0.1:8090 |

## Документация

| Документ | Содержание |
|---|---|
| [docs/android.md](docs/android.md) | Android-приложение: установка, функции, вольт, сборка, релизы |
| [docs/mobile-android.md](docs/mobile-android.md) | ТЗ и план этапов мобильного клиента (M0–M7) |
| [docs/architecture.md](docs/architecture.md) | Архитектура, потоки, тома, сети |
| [docs/plan.md](docs/plan.md) | Этапы и статус реализации |
| [docs/decisions.md](docs/decisions.md) | ADR — принятые решения |
| [docs/security.md](docs/security.md) | Секреты, доступ, изоляция, угрозы |
| [docs/operations.md](docs/operations.md) | Эксплуатация, бэкапы, обновления, перенос |
| [docs/portal.md](docs/portal.md) | Портал, регистрация, личные настройки, API вольта |
| [docs/obsidian.md](docs/obsidian.md) | Obsidian-вольты, права по папкам, синхронизация |
| [docs/telegram.md](docs/telegram.md) | Telegram-бот, allowlist, команды |
| [docs/voice.md](docs/voice.md) | Голос (STT/TTS) |
| [docs/search.md](docs/search.md) | Веб-поиск |
| [docs/mcp.md](docs/mcp.md) | MCP-серверы и инструменты |
| [docs/calendar-tasks.md](docs/calendar-tasks.md) | Календарь и задачи |
| [docs/proactive.md](docs/proactive.md) | Брифинг и проактивность |
| [docs/quick-access.md](docs/quick-access.md) | Быстрый доступ (mooo.com) |
| [docs/router.md](docs/router.md) | Роутер, DNS, HTTPS |
| [docs/vpn.md](docs/vpn.md) | VPN-прокси для исходящих соединений |
| [docs/rootless.md](docs/rootless.md) | Docker rootless |
| [docs/acceptance.md](docs/acceptance.md) | Критерии приёмки |

## Безопасность (кратко)

- Секреты только в `.env` и `secrets/` (600), не в git. `.env.example` — без
  реальных значений.
- Публичная регистрация Open WebUI выключена; регистрация — по токену через
  портал. Telegram — по allowlist/коду подтверждения.
- Права вольта и изоляция данных — на сервере. Docker rootless, ограниченные
  сети/лимиты, без публикации БД наружу.
- Опасные операции на хосте (sudo, apt, systemctl, fstab, диски) — только
  вручную и с подтверждением.

Подробнее: [docs/security.md](docs/security.md), [AGENTS.md](AGENTS.md).
