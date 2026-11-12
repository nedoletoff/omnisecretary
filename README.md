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
| 3 | Telegram-бот, allowlist, код подтверждения, Mini App | ⚠️ код готов, но api.telegram.org заблокирован в сети Pi (см. [docs/telegram.md](docs/telegram.md#если-apitelegramorg-заблокирован)) |
| 4 | MCP: Obsidian, GitHub, Filesystem, песочница, Vikunja | ✅ реализовано |
| 5 | Голос: Vosk-модель (sherpa-onnx) + Silero TTS, Piper как альтернатива | ✅ запущено и проверено |
| 6 | Проактивность: утренний брифинг, напоминания, PWA | ✅ брифинг собирается (отправка в Telegram ждёт доступного API) |
| 7 | Эксплуатация: HTTPS, бэкапы, мониторинг, напоминания | ✅ HTTPS выпущен и работает |
| 8 | VPN-прокси для заблокированных сервисов (Telegram) | ✅ работает (`make vpn-test`) |

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

# 4. Владелец, API-ключ, провайдер LLM и пресеты — одной командой
make init-webui

# 5. Создать пользователя Vikunja (задачи) и API-токен для MCP
make vikunja-admin

# 6. Собрать MCP-серверы и зарегистрировать инструменты
make mcp-build && make mcp-init
```

По умолчанию в `.env.example` уже прописана **бесплатная тестовая модель**
(Pollinations, без ключа). Ограничение бесплатного тарифа — 1 ответ в 45
секунд на IP (при превышении HTTP 402 `FREE_LIMIT`). Для продакшна замените
`OPENAI_API_BASE_URL` и `OPENAI_API_KEY` на своего провайдера и выполните
`make restart && make init-webui`.

`make init-webui` идемпотентен: повторный запуск не дублирует пользователя
и пресеты, а обновляет их. Пароль владельца и API-ключ лежат в
`${DATA_ROOT}/secrets/` (права 600) и в чат не печатаются.

`bootstrap-pi.sh` идемпотентен: повторный запуск не перезаписывает `.env`
и не ломает существующую установку. Все опасные шаги (`apt`, `systemctl`,
изменение `/etc/docker/daemon.json`) спрашивают подтверждение; `--yes`
отключает вопросы, `--dry-run` только печатает команды.

## Ручные шаги после установки

1. Открыть `https://nedoletoff.sknt.ru` (или `http://<ip-pi>` в LAN).
   **Логин `admin`**, пароль — в `/srv/omnisecretary/secrets/webui-owner.txt`:

   ```bash
   cat /srv/omnisecretary/secrets/webui-owner.txt
   ```

   Поле входа подписано «Email», но принимает логин `admin` — почта не нужна.
   Сменить пароль: Settings → Account. Если пароль потерян — сброс в Admin
   Panel → Users (владелец) или `make init-webui` (пересоздаст учётку).
2. Провайдер LLM уже настроен из `.env` (`make init-webui`). Для продакшна
   заменить `OPENAI_API_BASE_URL`/`OPENAI_API_KEY` и выполнить
   `make restart && make init-webui`.
3. Admin Panel → Settings → убедиться, что регистрация закрыта (`ENABLE_SIGNUP=false`).
4. Создать остальных пользователей вручную (Admin Panel → Users).
   У каждого свои чаты, память и файлы — данные не пересекаются.
5. Задачи: `make vikunja-admin`, войти на `http://<ip-pi>:8081`, сменить пароль
   и удалить файл `/srv/omnisecretary/secrets/vikunja-admin.txt`.
6. Пресеты уже импортированы (`make init-webui`). Повторный импорт:
   `WEBUI_API_KEY=$(cat ${DATA_ROOT}/secrets/webui-api-key.txt | cut -d= -f2) make presets ARGS="--base-model openai-fast"`.
7. **Obsidian-вольт**: открыть `http://<ip-pi>/vault/` (логин `owner`,
   пароль — `VAULT_ADMIN_PASSWORD` из `.env`), ввести URL приватного
   репозитория, ветку и PAT (`Contents: read/write`). Подробно —
   [docs/obsidian.md](docs/obsidian.md).
8. **GitHub**: вписать fine-grained PAT в `GITHUB_TOKEN` (`.env`), затем
   `make restart`. Права — [docs/mcp.md](docs/mcp.md).
9. Тема Gruvbox подключена автоматически; в Settings → Interface выберите Dark.
10. **Telegram**: получить токен у @BotFather, вписать в `.env`
    `TELEGRAM_BOT_TOKEN`, `TELEGRAM_OWNER_ID` (ваш числовой ID) и
    `TELEGRAM_ALLOWED_IDS` (только вы), затем `make tg-up`.
    Публичная регистрация и посторонние в боте запрещены — [docs/telegram.md](docs/telegram.md).
11. **Брифинг**: вписать `WEATHER_LAT`/`WEATHER_LON` своего города,
    проверить `make briefing`, затем `make proactive-up`.
    Напоминания в боте: `/remind 20:30 текст`.
12. **Голос**: `make voice-up`, в чате нажать микрофон. Если нужен лёгкий
    вариант — `make voice-piper` и `AUDIO_TTS_OPENAI_API_BASE_URL=http://tts-piper:8000/v1`.
13. **Перезагрузка** для memory-cgroup (параметры ядра уже прописаны):
    `sudo reboot`, после — `make smoke`.
14. Проверить `make smoke` (33 проверки).
15. Настроить бэкапы и health-alert по таймеру — [docs/operations.md](docs/operations.md).
16. Когда появится внешний диск — перенести `${DATA_ROOT}` по инструкции
    [docs/operations.md](docs/operations.md#переезд-на-внешний-диск).

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
| — | MCP-серверы | только внутри `backend`, портов нет |
| — | vault-manager | через nginx `/vault/`, Basic Auth, LAN |

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
make mcp-build   # собрать образ MCP-серверов
make mcp-init    # зарегистрировать инструменты в Open WebUI
make mcp-logs    # логи MCP-серверов
make tg-up       # запустить Telegram-бота
make voice-up    # запустить STT + Silero TTS (профиль voice)
make voice-piper # STT + Piper TTS (лёгкий вариант)
make voice-logs  # логи голосовых сервисов
make proactive-up    # утренний брифинг в Telegram
make briefing        # собрать брифинг сейчас
make briefing-send   # отправить брифинг в Telegram
./scripts/health-alert.sh  # smoke + алерт в Telegram при ошибке
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

## Тема Gruvbox

Интерфейс оформлен в Gruvbox: `theme/gruvbox.css` монтируется как
`/static/custom.css` (Open WebUI подключает его раньше основного бандла).
Палитра переопределяет CSS-переменные Tailwind (`--color-gray-*`,
`--color-blue-*` и т.д.), поэтому тема работает и в светлом, и в тёмном
режиме. Рекомендуется Settings → Interface → Theme → Dark.

Файл темы версионируется: правки — через git, применяются перезапуском
контейнера (`make restart`), сборка не нужна.

## Оптимизация под 4 ГБ ОЗУ
- Локальные LLM на Pi не запускаются (`ENABLE_OLLAMA_API=false`).
- Эмбеддинги — только облачные (`RAG_EMBEDDING_ENGINE=openai`).
- Векторы Qdrant хранятся на диске (`QDRANT_ON_DISK=true`).
- `DATABASE_ENABLE_SESSION_SHARING=False`.
- Лимиты памяти у каждого сервиса в `docker-compose.yml` (суммарно ~2,5 ГБ).
- Docker data-root и все тома — на диске данных, не на SD.
- Логи Docker ограничены (10 МБ × 3 на контейнер).
- Голос — отдельные контейнеры по профилю `voice`: STT ~200 МБ, TTS ~400 МБ
  (включаются только когда нужны).

## Документация

- [docs/architecture.md](docs/architecture.md) — сервисы, сети, тома, потоки данных.
- [docs/calendar-tasks.md](docs/calendar-tasks.md) — календарь, задачи, пресеты, режимы чатов.
- [docs/obsidian.md](docs/obsidian.md) — Obsidian-вольт: подключение, инструменты, правила записи.
- [docs/mcp.md](docs/mcp.md) — MCP-серверы, права, песочница, добавление своего.
- [docs/voice.md](docs/voice.md) — русский STT (Vosk/sherpa-onnx) и TTS (Silero, Piper).
- [docs/proactive.md](docs/proactive.md) — утренний брифинг, PWA, ограничения.
- [docs/operations.md](docs/operations.md) — эксплуатация, бэкапы, переезд на диск, мониторинг.
- [docs/vpn.md](docs/vpn.md) — VPN-прокси, выбор Auto-сервера, трафик
- [docs/telegram.md](docs/telegram.md) — бот, allowlist, код подтверждения, Mini App.
- [docs/search.md](docs/search.md) — веб-поиск для моделей, движки, экономия токенов.
- [docs/router.md](docs/router.md) — проброс портов, DNS, HTTPS, Telegram Mini App.
- [docs/plan.md](docs/plan.md) — этапы и критерии перехода.
- [docs/decisions.md](docs/decisions.md) — принятые решения и допущения.
- [docs/security.md](docs/security.md) — секреты, доступ, подтверждения.
- [docs/acceptance.md](docs/acceptance.md) — критерии готовности из ТЗ.
- [AGENTS.md](AGENTS.md) — правила работы для AI-агентов.

## Лицензия

Личный проект, без публичной лицензии.
