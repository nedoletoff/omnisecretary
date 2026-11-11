# Пресеты-«лица» OmniSecretary

Каждый файл — один пресет Open WebUI: системный промпт, температура и
возможности. Пресет создаётся как «модель» в Workspace → Models и
наследует базовую LLM.

| Файл | Пресет | Назначение | Инструменты (MCP) |
|---|---|---|---|
| `secretary.md` | Secretary | календарь, задачи, заметки, напоминания | Vikunja, Files |
| `omni.md` | Omni | универсальный ассистент | Vikunja, Files, Obsidian |
| `archivist.md` | Archivist | Obsidian-вольт: поиск и запись | Obsidian |
| `coder.md` | Coder | код, git, GitHub, песочница | GitHub, Sandbox, Files |
| `private.md` | Private | без памяти, истории и инструментов | — |

## Формат файла

```
---
id: secretary            # id модели, латиницей
name: Secretary          # отображаемое имя
description: ...         # описание в списке моделей
temperature: 0.4
citations: false         # capabilities
web_search: false
code_interpreter: false
vision: false
tools: vikunja, files     # имена MCP-серверов → server:mcp:<имя>
---

<системный промпт>
```

## Импорт

1. Добавьте провайдера LLM в Open WebUI (Settings → Connections) и узнайте
   id модели, например `deepseek-chat`.
2. Создайте API-ключ: Settings → Account → API Keys.
3. Запустите:

```bash
WEBUI_API_KEY=sk-... ./scripts/import-presets.sh --base-model deepseek-chat
# или через make:
WEBUI_API_KEY=sk-... make presets ARGS="--base-model deepseek-chat"
```

Без `WEBUI_API_KEY` скрипт только соберёт `presets/models.json` — его можно
импортировать вручную: Workspace → Models → Import.

Повторный импорт обновляет пресеты по `id`, дубликаты не создаются.
Список `tools` превращается в `meta.toolIds`; повторный импорт не теряет
привязку инструментов. После добавления нового MCP-сервера:
`make mcp-init` (он проставит инструменты по матрице из `scripts/init-mcp.py`).

## Режимы чатов

| Режим | Как включить | Память/история |
|---|---|---|
| Обычный | просто чат | память и история включены |
| Временный | «Temporary Chat» в меню чата | не сохраняется в БД |
| Приватный | Temporary Chat + пресет Private | без памяти, знаний и инструментов |
| Ограниченная память | пресет с отключённым RAG/памятью | история есть, память нет |

Подробнее — `docs/calendar-tasks.md` и `docs/plan.md` (этап 2).
