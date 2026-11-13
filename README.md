# OmniSecretary

Self-hosted персональный AI-секретарь на Raspberry Pi 4 (4 ГБ ОЗУ).
Один бэкенд — много окон: веб-интерфейс, Telegram, API, голос.

Всё работает на своём железе. LLM — в облаке через недорогие
OpenAI-совместимые API; эмбеддинги для памяти считаются локально
(`all-MiniLM-L6-v2`). Pi отвечает за интерфейс, память, инструменты и голос.

## Статус

| Этап | Содержание | Статус |
|---|---|---|
| 1 | Ядро: Open WebUI + PostgreSQL + Qdrant + Redis + Nginx, скрипты | ✅ реализовано |
| 2 | Календарь Open WebUI, задачи (Vikunja), пресеты-«лица» | ✅ реализовано |
| 3 | Telegram-бот, allowlist, код подтверждения, Mini App | ✅ @nd_omnisecretarybot работает через VPN |
| 4 | MCP: Obsidian, GitHub, Filesystem, песочница, Vikunja | ✅ реализовано |
| 5 | Голос: Vosk-модель (sherpa-onnx) + Silero TTS, Piper как альтернатива | ✅ запущено и проверено |
| 6 | Проактивность: утренний брифинг, напоминания, PWA | ✅ брифинг отправляется в Telegram |
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
- Ключ облачного OpenAI-совместимого API для LLM (эмбеддинги локальные).

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

## Кто такой ассистент

Пресет `omnisecretary` — основной: ассистент знает, что работает внутри
OmniSecretary на Pi, перечисляет свои возможности (календарь, задачи,
Obsidian, поиск, GitHub, голос, брифинг), соблюдает приватность и умеет
провести по настройке. Тот же промпт применяется к базовой модели, поэтому
даже без выбора пресета ассистент не считает себя обычным чатом.

## Вход и регистрация

**Администратор.** Логин `nedoletoff`. Пароль — в `secrets/webui-owner.txt`,
посмотреть: `make owner-creds`.

**Новые пользователи.** Публичная регистрация Open WebUI закрыта. Аккаунт
создаётся на портале:

1. Открыть `https://nedoletoff.sknt.ru/register` (или `/portal/register`).
2. Ввести логин (3–32 символа: латиница, цифры, `.`, `-`, `_`), пароль
   дважды, затем **токен приглашения** — это `TELEGRAM_CONFIRM_CODE` из `.env`
   (16 символов). Хотите отдельный токен — задайте `REGISTER_TOKEN`.
3. После создания войти на `/portal/login` тем же логином и паролем —
   портал откроет чат уже авторизованным.

На странице `/portal/account` пользователь задаёт личные настройки: город для
погоды, Telegram ID, GitHub-токен и пресет по умолчанию. История чатов у
каждого своя: Open WebUI разделяет чаты по аккаунту, приватный режим ничего
не сохраняет.

Язык интерфейса по умолчанию — русский (`DEFAULT_LOCALE=ru-RU`), но это не
жёстко: каждый может сменить в Settings → Interface.

## Безопасность

- HTTP на внешнем домене отвечает **301 на HTTPS**, HSTS включён.
- Пароли и коды защищены таймаутами: 5 ошибок → пауза (15 мин на портале,
  10 мин в боте). Лимитов запросов нет.
- Сети Docker разделены: песочница не видит MCP-серверы с GitHub-токеном
  и вольтом.
- Redis с паролем, CORS ограничен доменом, токен Open WebUI живёт 30 дней.
- Файлы с личными данными (`portal.db`, `bot.db`, бэкапы) — 600/700.
- Усиление хоста выполнено: UFW активен (22/80/443, 8081 — только LAN),
  автообновления безопасности включены. Повторный запуск идемпотентен:
  `sudo ./scripts/harden-host.sh`.

Подробности и модель угроз — [docs/security.md](docs/security.md).

## Первый вход и начало работы

Всё уже запущено. Ниже — что открыть и где взять доступы.

### 1. Веб-интерфейс

- Адрес: **https://nedoletoff.sknt.ru** (в LAN: `http://192.168.0.133`).
- Логин: **`nedoletoff`**. Поле подписано «Email», но принимает логин — почта не нужна.
- Пароль:

  ```bash
  make owner-creds
  # или: cat /srv/omnisecretary/secrets/webui-owner.txt
  ```

- Сменить пароль: Settings → Account. Потеряли — Admin Panel → Users
  или `make init-webui` (пересоздаст учётку владельца).

### 2. Регистрация новых пользователей

1. Открыть **https://nedoletoff.sknt.ru/register**.
2. Логин (3–32 символа), пароль дважды, **токен приглашения**.
3. Токен — `TELEGRAM_CONFIRM_CODE` в `.env`, он же в
   `/srv/omnisecretary/secrets/telegram-confirm-code.txt` (16 символов).
4. Вход — `/portal/login`; личные настройки (город, Telegram ID, GitHub-токен,
   пресет) — `/portal/account`.

Подробно: [docs/portal.md](docs/portal.md).

### 3. Telegram

- Бот: **@nd_omnisecretarybot** (работает через VPN-прокси).
- Нажать Start → бот попросит код подтверждения → отправить код из
  `secrets/telegram-confirm-code.txt`. Доступ сохраняется навсегда.
- Команды: `/app` — веб-интерфейс внутри Telegram, `/weather` — погода,
  `/new`, `/preset`, `/remind 20:30 текст`, `/users` (только владелец).
- Владелец: `TELEGRAM_OWNER_ID=278466638`; посторонние без кода не пускаются.

Подробно: [docs/telegram.md](docs/telegram.md).

### 4. Задачи (Vikunja)

- Адрес: **http://192.168.0.133:8081** (наружу не пробрасывается).
- Логин `admin`, пароль — `/srv/omnisecretary/secrets/vikunja-admin.txt`.
  Смените пароль в интерфейсе и удалите файл.
- CalDAV для телефона: `http://192.168.0.133:8081/dav/`.
- Календарь событий — раздел «Календарь» в Open WebUI.

Подробно: [docs/calendar-tasks.md](docs/calendar-tasks.md).

### 5. Obsidian-вольт

- Открыть **https://nedoletoff.sknt.ru/vault/**.
- Логин `owner`, пароль — `secrets/vault-admin.txt` или `VAULT_ADMIN_PASSWORD`
  в `.env`.
- Уже подключён `nedoletoff/obsidian_vault`, ветка `main`
  (`/vault/status` → `ready: true`). Сменить репозиторий можно той же формой.
- Доступ только у владельца; записи ИИ — только в `AI/`, каждая с коммитом,
  только по прямой просьбе.

Подробно: [docs/obsidian.md](docs/obsidian.md).

### 6. Модель LLM

- Сейчас подключена бесплатная Pollinations (`openai-fast`): 1 ответ в 45 с.
- Память работает локально: эмбеддинги `all-MiniLM-L6-v2`, ключ не нужен.
- Для нормальной скорости — платный OpenAI-совместимый провайдер: заменить
  `OPENAI_API_BASE_URL`, `OPENAI_API_KEY` в `.env`, затем
  `make restart && make init-webui`.
- Пресеты: Secretary, Omni, OmniSecretary, Archivist, Coder, Private.

Подробно: [docs/mcp.md](docs/mcp.md), [docs/architecture.md](docs/architecture.md).

### 7. Погода и локация

- По умолчанию: Санкт-Петербург, Ломоносовская (`WEATHER_LAT`/`WEATHER_LON`).
- Своя локация: `/weather Казань` в боте или поле «Город» на `/portal/account`.

### 8. Голос

```bash
make voice-up     # STT sherpa-onnx (русский) + TTS Silero
```

В чате — микрофон и кнопка озвучки. Альтернатива: `make voice-piper`.

Подробно: [docs/voice.md](docs/voice.md).

### 9. Брифинг и напоминания

```bash
make briefing       # собрать и отправить сейчас
make proactive-up   # включить ежедневную отправку
```

Подробно: [docs/proactive.md](docs/proactive.md).

### 10. Веб-поиск

Включён для Secretary, Omni, OmniSecretary, Archivist (DuckDuckGo, без ключа).
Отключён для Coder и Private. Смена движка — [docs/search.md](docs/search.md).

### 11. Проверка и эксплуатация

```bash
make ps         # статус контейнеров
make smoke      # 44 проверки: сервисы, HTTP, БД, модели, MCP, голос
make logs       # логи
```

- Бэкапы, мониторинг, переезд на внешний диск — [docs/operations.md](docs/operations.md).
- Усиление хоста выполнено (UFW + автообновления); повтор — `sudo ./scripts/harden-host.sh`.
- Роутер, DNS, HTTPS, продление сертификата — [docs/router.md](docs/router.md).
- VPN-прокси и выбор сервера — [docs/vpn.md](docs/vpn.md).

### 12. Где лежат доступы

| Что | Где |
|---|---|
| Пароль владельца Open WebUI | `/srv/omnisecretary/secrets/webui-owner.txt` (`make owner-creds`) |
| Код приглашения (бот и регистрация) | `.env` → `TELEGRAM_CONFIRM_CODE`; `secrets/telegram-confirm-code.txt` |
| Пароль Vikunja | `/srv/omnisecretary/secrets/vikunja-admin.txt` |
| Пароль формы вольта | `secrets/vault-admin.txt`; `.env` → `VAULT_ADMIN_PASSWORD` |
| API-ключ Open WebUI (для `make presets`) | `/srv/omnisecretary/secrets/webui-api-key.txt` |
| GitHub PAT вольта | создаётся формой `/vault/` → `secrets/vault-git-token` |
| Все секреты | `.env` (600, в git не попадает) |
| Данные | `/srv/omnisecretary` (`DATA_ROOT`) |

## Документация: что где лежит

| Документ | О чём |
|---|---|
| [docs/architecture.md](docs/architecture.md) | сервисы, сети, порты, тома, потоки данных |
| [docs/security.md](docs/security.md) | секреты, доступы, таймауты, модель угроз |
| [docs/portal.md](docs/portal.md) | регистрация по токену, вход, личные настройки |
| [docs/telegram.md](docs/telegram.md) | бот, allowlist, код, Mini App |
| [docs/calendar-tasks.md](docs/calendar-tasks.md) | календарь, Vikunja, CalDAV, пресеты |
| [docs/obsidian.md](docs/obsidian.md) | вольт: подключение, инструменты, правила записи |
| [docs/mcp.md](docs/mcp.md) | MCP-серверы, права, песочница |
| [docs/voice.md](docs/voice.md) | русский STT и TTS |
| [docs/proactive.md](docs/proactive.md) | брифинг, напоминания, PWA |
| [docs/search.md](docs/search.md) | веб-поиск, движки, экономия токенов |
| [docs/router.md](docs/router.md) | роутер, порты, DNS, HTTPS, сертификаты |
| [docs/vpn.md](docs/vpn.md) | VPN-прокси, выбор сервера, трафик |
| [docs/operations.md](docs/operations.md) | бэкапы, обновления, переезд на диск |
| [docs/decisions.md](docs/decisions.md) | принятые решения и допущения (ADR) |
| [docs/plan.md](docs/plan.md) | этапы и критерии перехода |
| [docs/acceptance.md](docs/acceptance.md) | критерии готовности из ТЗ |
| [AGENTS.md](AGENTS.md) | правила для AI-агентов |

## Порты

| Порт | Кто слушает | Доступ |
|---|---|---|
| 80 | nginx | редирект на HTTPS (кроме ACME и /healthz) |
| 443 | nginx | LAN и интернет, Let's Encrypt |
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
- Эмбеддинги — локальные `all-MiniLM-L6-v2` (`RAG_EMBEDDING_ENGINE=`), ~400 МБ ОЗУ, без ключа.
- Векторы Qdrant хранятся на диске (`QDRANT_ON_DISK=true`).
- `DATABASE_ENABLE_SESSION_SHARING=False`.
- Лимиты памяти у каждого сервиса в `docker-compose.yml` (Open WebUI 2 ГБ, суммарно ~1,7 ГБ фактически).
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
- [docs/portal.md](docs/portal.md) — регистрация по токену, вход, личные настройки
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
