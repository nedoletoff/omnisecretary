# Эксплуатация

## Запуск и управление

```bash
make up            # поднять стек
make down          # остановить
make restart       # перезапуск
make ps            # статус контейнеров
make logs          # логи
make smoke         # проверка здоровья (в т.ч. API, сервисы)
```

Профили: `telegram`, `proactive`, `voice`, `voice-piper`, `ntfy`, `mcp` (по
конфигурации). Примеры: `make tg-up`, `make proactive-up`, `make voice-up`.

Rootless Docker: команды выполняются без `sudo`, контекст `rootless`.

## Обновление

```bash
make update        # пересборка/обновление по зафиксированным тегам и скриптам
```

Образы фиксированы в `docker-compose.yml`. Обновление тегов — отдельный
коммит после проверки. После обновления: `make smoke`, при необходимости
`make init-webui` / `make mcp-init`.

## Бэкапы

```bash
make backup
```

Создаёт дамп PostgreSQL, снапшот Qdrant и метаданные в `${DATA_ROOT}/backups`
(права ограничены). Рекомендуется регулярный запуск (cron) и хранение копий
отдельно от Pi.

Восстановление — по скриптам/инструкциям для конкретных компонентов; перед
восстановлением остановить зависимые сервисы.

## Сертификаты TLS

- Сертификаты: Let's Encrypt, webroot/certbot.
- Продление: `make renew-certs` (cron настроен, лог в `${DATA_ROOT}/logs`).
- При rootless копии сертификатов синхронизируются в доступное для nginx
  расположение (`scripts/sync-certs.sh`).

Проверка: `curl -I https://nedoletoff.sknt.ru`, срок — `openssl` или certbot.

## Данные и перенос на диск

- `DATA_ROOT` (по умолчанию `/srv/omnisecretary`) содержит тома, секреты,
  бэкапы, вольты, логи. Для переноса на внешний диск:
  1. остановить стек, сделать бэкап;
  2. перенести данные, обновить `DATA_ROOT`/маунты;
  3. при необходимости перенастроить Docker data-root;
  4. поднять стек, `make smoke`.
- Вольты пользователей: `${DATA_ROOT}/vaults/<user_id>` и индексы — включать
  в резервное копирование.

## Персональные Obsidian-вольты

- Настройка/синхронизация — пользователем через приложение (`/api/m/vault/*`)
  или API портала.
- Серверный вольт владельца: `/vault/status`, `/vault/sync` (Basic Auth),
  логи `omni-vault-manager`.
- При ошибках push — проверить токен, права репозитория, доступность GitHub,
  статус синхронизации.

## Android-приложение

- Релизы: [GitHub Releases](https://github.com/nedoletoff/omnisecretary/releases),
  тег `android-v*`. Сборка — GitHub Actions.
- Обновление сервера может требовать совместимого клиента; при изменении
  `/api/m/*` — выпуск новой версии приложения.
- CI: при падении сборки — смотреть лог шага «Сборка debug APK»; для релиза
  нужны `permissions: contents: write` и (для подписи) секреты `RELEASE_*`.

## VPN и исходящие соединения

- Конфиг: `${DATA_ROOT}/vpn/config.json`, управление — `scripts/vpn-config.py`,
  `make vpn-*`. Проверка: `make vpn-test`.
- Через VPN: telegram-bot, proactive, llm-gateway (по настройке). Локальные
  адреса — в `NO_PROXY`.

## Мониторинг

- `make ps`, `make smoke`, логи контейнеров.
- Панель владельца: `/portal/admin/status` (температура, память, диски,
  контейнеры, сертификат, бэкап, VPN/UFW).
- Telegram-бот: логи `omni-telegram-bot`; при проблемах — сеть/VPN, токен.

## Типовые проблемы

| Симптом | Проверка |
|---|---|
| Контейнер unhealthy | `docker logs <name>`, лимиты памяти, зависимости |
| 502/недоступен домен | nginx, DNS, проброс портов, `make smoke` |
| Нет ответов от LLM | ключи/баланс провайдера, `llm-gateway`, сеть/VPN |
| Не приходит в Telegram | доступ к `api.telegram.org`, VPN, токен, allowlist/код |
| Вольт не синхронизируется | PAT, права repo, статус `/api/m/vault/config` или `/vault/status` |
| Ошибка прав доступа к вольту | правила папок (персональный вольт), настроенный путь |

## Безопасное обслуживание

- Секреты не копировать в публичные места. Перед публикацией логов —
  удалить токены/пароли.
- Изменения на хосте (пакеты, systemd, сеть, fstab) — с пониманием последствий
  и бэкапом.
- См. [security.md](security.md), [AGENTS.md](AGENTS.md).
