# MCP-инструменты

Все инструменты — **внешние контейнеры** с MCP-серверами (streamable HTTP),
а не in-process плагины. Так их можно перезапускать и ограничивать по памяти
отдельно от ядра.

| Сервис | Контейнер | Инструменты | Память |
|---|---|---|---|
| `mcp-vault` | `omni-mcp-vault` | Obsidian-вольт: поиск, чтение, запись в `AI/` | 128 МБ |
| `mcp-files` | `omni-mcp-files` | общий каталог `${DATA_ROOT}/shared` | 64 МБ |
| `mcp-github` | `omni-mcp-github` | GitHub REST: файлы, коммиты, issues, PR | 96 МБ |
| `mcp-vikunja` | `omni-mcp-vikunja` | задачи Vikunja | 64 МБ |
| `mcp-sandbox` | `omni-mcp-sandbox` | песочница: python/node/git в `/workspace` | 192 МБ |

Все — в сети `backend`, порты не публикуются, `no-new-privileges`,
логи ограничены 10 МБ × 3.

## Регистрация в Open WebUI

```bash
make mcp-build   # собрать образ (python:3.12-slim + mcp + git)
make mcp-init    # зарегистрировать серверы и раздать инструменты пресетам
```

`make mcp-init` идемпотентен: перезаписывает список tool server connections,
проверяет связь с каждым сервером (`/api/v1/configs/tool_servers/verify`)
и проставляет пресетам `meta.toolIds`.

Важно: `tool_ids` передаются в запросе **на верхнем уровне** (`tool_ids`),
а не в `metadata` — иначе Open WebUI их игнорирует.

## Кто что видит

| Пресет | Инструменты |
|---|---|
| Secretary | Vikunja, Files |
| Omni | Vikunja, Files, Obsidian |
| Archivist | Obsidian |
| Coder | GitHub, Sandbox, Files |
| Private | нет |

Принцип минимальных прав: пресет получает только то, что нужно для его роли.
`Private` не получает ничего — приватный чат не должен уметь писать в вольт.

Tool servers зарегистрированы **без `access_grants`** → их видит только
администратор (владелец). Обычные пользователи инструментов не получают.

## GitHub: токен и права

Токен — fine-grained PAT, только в `.env` (`GITHUB_TOKEN`), не в compose.

Минимальные права:

- **Contents**: read/write — только для нужных репозиториев;
- **Issues**: read/write;
- **Pull requests**: read/write;
- **Metadata**: read (обязательное).

Не выдавать: Administration, Secrets, Actions, Webhooks, Organizations.
Срок — 90 дней, затем ротация. Подробнее — [security.md](security.md).

Если токен не задан, `gh_*` возвращают «GITHUB_TOKEN не задан» — сервер
остаётся живым, просто без операций.

## Vikunja

`make vikunja-admin` создаёт пользователя и API-токен (сохраняется в
`${DATA_ROOT}/secrets/vikunja-api-token.txt` и `.env` как `VIKUNJA_API_TOKEN`).
Права токена: `projects: read_all`, `tasks: read_all/create/update`,
`labels: read_all`. Срок — до 2030-01-01, ротация — вручную.

Инструменты: `vk_projects`, `vk_tasks`, `vk_create_task`, `vk_update_task`,
`vk_complete_task`, `vk_today` (просроченные и на сегодня).

## Песочница

`mcp-sandbox` позволяет запускать код и прототипы, но **не даёт доступа к
хосту**:

- нет docker.sock, нет `privileged`, нет host-сети;
- только каталог `/workspace` (`${DATA_ROOT}/sandbox`);
- список разрешённых программ: `ls, cat, head, tail, wc, grep, find, pwd,
  echo, python3, python, pip, node, npm, git, date, uname, df, free, ps,
  which, mkdir, touch, cp, mv`;
- запрещены `sudo, su, apt, dpkg, systemctl, service, rm, rmdir, dd, mkfs,
  fdisk, mount, umount, shutdown, reboot, docker, chmod, chown, chroot,
  nsenter, iptables`;
- запрещены метасимволы оболочки (`; | & > < \` $`);
- пути за пределами `/workspace` и `/tmp` отклоняются.

Опасные операции на самом Pi (`sudo`, `apt`, `systemctl`, правка
`/etc/fstab`, форматирование) сознательно **не автоматизированы**: их
выполняет владелец вручную. Это соответствует правилу из `AGENTS.md`.

## Файлы

`${DATA_ROOT}/shared` — общая папка для обмена файлами с ассистентом:
`files_list`, `files_read`, `files_search`, `files_write`. Все пути
ограничены каталогом, выход за пределы запрещён.

## Календарь

Встроенный календарь Open WebUI (`/api/v1/calendars`) — события.
Отдельного CalDAV-MCP нет: в v0.13.3 у Open WebUI нет CalDAV-синхронизации,
а Vikunja закрывает задачи по CalDAV (`/dav/`). Мост событий CalDAV —
открытый вопрос, см. ADR-016.

## Добавить свой MCP-сервер

1. Положить сервер в `mcp/servers/` (FastMCP, `streamable_http_path="/mcp"`).
2. Добавить сервис в `docker-compose.yml` по образцу `mcp-files`
   (образ `omnisecretary-mcp:local`, `mem_limit`, healthcheck, сеть `backend`).
3. Добавить запись в `SERVERS` в `scripts/init-mcp.py` и, если нужно,
   в `PRESET_TOOLS`.
4. `make mcp-build && make mcp-init && make smoke`.

Правило: сервер без `access_grants` доступен только владельцу. Не выдавайте
инструменты с доступом к shell/GitHub обычным пользователям.

## Проверка

```bash
make smoke        # в т.ч. «MCP: 5 серверов зарегистрированы»
make mcp-logs     # логи всех MCP-серверов
```

Проверка инструмента вручную (внутри сети backend):

```bash
docker run --rm --network omnisecretary_backend omnisecretary-mcp:local \
  python -c "
import asyncio
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
async def main():
    async with streamablehttp_client('http://mcp-vikunja:8000/mcp') as (r,w,_):
        async with ClientSession(r,w) as s:
            await s.initialize()
            print((await s.call_tool('vk_projects', {})).content[0].text)
asyncio.run(main())"
```
