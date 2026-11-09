# OmniSecretary

Self-hosted персональный AI-секретарь на Raspberry Pi 4 (4 ГБ ОЗУ).
Один бэкенд — много окон: веб-интерфейс, Telegram, API, голос.

Всё работает на своём железе. Тяжёлые вычисления (LLM, эмбеддинги) — в облаке
через недорогие OpenAI-совместимые API; Pi отвечает за интерфейс, память,
инструменты и голос.

## Статус

| Этап | Содержание | Статус |
|---|---|---|
| 1 | Ядро: Open WebUI + PostgreSQL + Qdrant + Redis + Nginx, скрипты | ✅ реализовано |
| 2 | Календарь Open WebUI, задачи (Vikunja), пресеты-«лица» | ✅ реализовано |
| 3 | Telegram-бот, allowlist, многопользовательский режим | ⏳ следующий |
| 4 | MCP: Obsidian, GitHub, Filesystem, SSH, CalDAV, Vikunja | ⏳ |
| 5 | Голос: Vosk STT + Silero TTS | ⏳ |
| 6 | Проактивность: утренний брифинг, напоминания, PWA | ⏳ |

Подробный план и критерии перехода между этапами — [docs/plan.md](docs/plan.md).

## Архитектура (кратко)

```
Интернет/LAN
     │  :80 / :443            :8081
 ┌───▼──────────┐        ┌────────────┐
 │    nginx     │───────▶│  Vikunja   │  задачи, CalDAV
 └───┬──────────┘        └─────┬──────┘
     │ frontend                │ backend
 ┌───▼──────────┐        ┌─────▼──────┐
 │  open-webui  │───────▶│ PostgreSQL │  чаты, календарь, задачи
 │   (ядро)     │        └────────────┘
 │              │───────▶│  Qdrant    │  векторная память
 │              │───────▶│   Redis    │  веб-сокеты, кэш
 └──────────────┘        └────────────┘
        backend (внутренняя сеть, наружу не выходит)
```

Подробно: [docs/architecture.md](docs/architecture.md).

## Требования

- Raspberry Pi 4 / 4 ГБ, Raspberry Pi OS Lite 64-bit (Debian 13) или Ubuntu Server 24.04 ARM64.
- Диск данных: внешний HDD/SSD в `/mnt/data` (рекомендуется) либо SD-карта.
- Docker Engine + Compose v2 (ставит `scripts/bootstrap-pi.sh`).
- Ключи облачных OpenAI-совместимых API (LLM + эмбеддинги).

## Быстрый старт

```bash
git clone <repo> /opt/omnisecretary   # или текущий каталог
cd /opt/omnisecretary

# 1. Посмотреть, что будет сделано (ничего не меняет)
./scripts/bootstrap-pi.sh --dry-run

# 2. Установка: Docker, data-root, каталоги, .env с секретами, запуск
./scripts/bootstrap-pi.sh

# 3. Вписать ключи LLM в .env, затем:
make restart && make smoke

# 4. Создать администратора Vikunja (задачи)
make vikunja-admin
```

`bootstrap-pi.sh` идемпотентен: повторный запуск не перезаписывает `.env`
и не ломает существующую установку. Все опасные шаги (`apt`, `systemctl`,
изменение `/etc/docker/daemon.json`) спрашивают подтверждение; `--yes`
отключает вопросы, `--dry-run` только печатает команды.

## Ручные шаги после установки

1. Открыть `http://<ip-pi>` и создать первого пользователя — он станет администратором.
2. В `.env` заполнить `OPENAI_API_BASE_URL`, `OPENAI_API_KEY`, `EMBEDDING_API_BASE_URL`,
   `EMBEDDING_API_KEY`, `EMBEDDING_MODEL`; выполнить `make restart`.
3. Admin Panel → Settings → убедиться, что регистрация закрыта (`ENABLE_SIGNUP=false`).
4. Создать остальных пользователей вручную (Admin Panel → Users).
5. Задачи: `make vikunja-admin`, войти на `http://<ip-pi>:8081`, сменить пароль
   и удалить файл `/srv/omnisecretary/secrets/vikunja-admin.txt`.
6. Пресеты: создать API-ключ (Settings → Account → API Keys) и выполнить
   `WEBUI_API_KEY=... make presets ARGS="--base-model <id модели>"`.
7. Проверить `make smoke`.
8. Настроить бэкапы в cron (см. ниже).

## Порты

| Порт | Кто слушает | Доступ |
|---|---|---|
| 80 | nginx | LAN / Cloudflare Tunnel |
| 443 | nginx | LAN, при настроенном TLS |
| 8081 | nginx → Vikunja | LAN, наружу не пробрасывать |
| 6333 | Qdrant | только `127.0.0.1` (бэкапы, диагностика) |
| 5432 | PostgreSQL | только внутри `backend` |
| 6379 | Redis | только внутри `backend` |
| 8080 | Open WebUI | только внутри `frontend` |
| 3456 | Vikunja | только внутри Docker-сетей |

## Команды

```bash
make help       # список целей
make up         # запустить
make ps         # статус
make logs       # логи
make smoke      # проверка здоровья
make backup     # бэкап
make update     # обновление по фиксированным тегам
make reload     # перечитать конфиг nginx
make vikunja-admin  # создать администратора задач
make presets ARGS="--base-model <id>"  # импорт пресетов (нужен WEBUI_API_KEY)
```

## Бэкапы

```bash
./scripts/backup.sh                 # pg_dump + снапшоты Qdrant + git-синхронизация вольта
./scripts/backup.sh --with-files    # плюс файлы Open WebUI
```

Рекомендуемый cron (ежедневно в 04:30):

```cron
30 4 * * * cd /opt/omnisecretary && ./scripts/backup.sh >> /srv/omnisecretary/logs/backup.log 2>&1
```

Хранится 14 последних бэкапов (меняется `--keep N`). Бэкапы лежат вне git
в `${DATA_ROOT}/backups`. Секреты в бэкап не попадают.

## Оптимизация под 4 ГБ ОЗУ

- Локальные LLM на Pi не запускаются (`ENABLE_OLLAMA_API=false`).
- Эмбеддинги — только облачные (`RAG_EMBEDDING_ENGINE=openai`).
- Векторы Qdrant хранятся на диске (`QDRANT_ON_DISK=true`).
- `DATABASE_ENABLE_SESSION_SHARING=False`.
- Лимиты памяти у каждого сервиса в `docker-compose.yml` (суммарно ~2,5 ГБ).
- Docker data-root и все тома — на диске данных, не на SD.
- Логи Docker ограничены (10 МБ × 3 на контейнер).

## Документация

- [docs/architecture.md](docs/architecture.md) — сервисы, сети, тома, потоки данных.
- [docs/calendar-tasks.md](docs/calendar-tasks.md) — календарь, задачи, пресеты, режимы чатов.
- [docs/obsidian.md](docs/obsidian.md) — требования к Obsidian-вольту.
- [docs/plan.md](docs/plan.md) — этапы и критерии перехода.
- [docs/decisions.md](docs/decisions.md) — принятые решения и допущения.
- [docs/security.md](docs/security.md) — секреты, доступ, подтверждения.
- [docs/acceptance.md](docs/acceptance.md) — критерии готовности из ТЗ.
- [AGENTS.md](AGENTS.md) — правила работы для AI-агентов.

## Лицензия

Личный проект, без публичной лицензии.
