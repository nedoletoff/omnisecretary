# Веб-поиск для моделей

Ассистент умеет искать в интернете и отвечать со ссылками на источники.
Поиск выполняет Open WebUI, модель получает готовые сниппеты — отдельный
сервис не нужен.

## Как включено

`docker-compose.yml` (сервис `open-webui`):

```yaml
ENABLE_WEB_SEARCH: "true"
WEB_SEARCH_ENGINE: duckduckgo
WEB_SEARCH_RESULT_COUNT: "3"
WEB_SEARCH_CONCURRENT_REQUESTS: "1"
```

Движок и лимиты продублированы в конфиге Open WebUI (БД). Если меняете
переменные на уже работающей установке, примените их через Admin Panel →
Settings → Web Search либо импортом конфига (см. ниже): env читается только
при первом создании конфига.

## Почему DuckDuckGo

Проверено с Pi 11.11.2026:

| Движок | Ответ | Время |
|---|---|---|
| DuckDuckGo (html) | 200 | 1,2 с |
| Bing | 200 | 0,4 с |
| searx.be (JSON) | 200 | 0,6 с |
| Yandex | 302 (антибот) | — |
| open-meteo / alphacephei | нет маршрута | — |

DuckDuckGo работает без ключа и отдаёт русские источники. `WEB_SEARCH_RESULT_COUNT=3`
и `CONCURRENT_REQUESTS=1` — экономия токенов и памяти.

Проверка поиска (без модели):

```bash
KEY=$(grep -m1 '^WEBUI_API_KEY=' .env | cut -d= -f2-)
curl -s -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d '{"queries":["погода Москва сегодня"]}' \
  http://127.0.0.1/api/v1/retrieval/process/web/search | head -c 300
```

## Кто получает поиск

В `presets/*.md`:

| Пресет | `web_search` | `function_calling` |
|---|---|---|
| Secretary | true | legacy (RAG-поиск) |
| Omni | true | legacy |
| Archivist | true | legacy |
| Coder | false | native (MCP-инструменты) |
| Private | false | — |

`function_calling: legacy` включает принудительный поиск по флагу
`features.web_search` — модель не обязана поддерживать tool calling
(Pollinations его не поддерживает). У Coder наоборот: нативные инструменты
MCP важнее поиска.

## Как переключить движок

| Движок | Что задать |
|---|---|
| Brave | `WEB_SEARCH_ENGINE=brave`, `BRAVE_SEARCH_API_KEY` |
| Tavily | `WEB_SEARCH_ENGINE=tavily`, `TAVILY_API_KEY` |
| SearXNG | `WEB_SEARCH_ENGINE=searxng`, `SEARXNG_QUERY_URL=http://searxng:8080/search?q=<query>&format=json` |
| Bing | `WEB_SEARCH_ENGINE=bing`, `BING_SEARCH_V7_SUBSCRIPTION_KEY` |

Ключи — только в `.env`. После смены: `make restart`, затем проверить
поиск curl-командой выше.

## Экономия токенов

- 3 результата на запрос, последовательные запросы (`CONCURRENT_REQUESTS=1`).
- `WEB_SEARCH_BYPASS_EMBEDDING_AND_RETRIEVAL=true`: сниппеты идут прямо в
  контекст, без эмбеддингов и записи в Qdrant.
- Поиск не включён у Coder и Private.
- Бесплатная тестовая модель Pollinations ограничена 1 ответом в 45 секунд
  на IP (при превышении — HTTP 402 `FREE_LIMIT`). Для реальной работы
  подключите платный OpenAI-совместимый API.

## Ограничения

- Нет кэша результатов: повторный вопрос снова ходит в поиск.
- DuckDuckGo может отдавать капчу при частых запросах — тогда
  `WEB_SEARCH_CONCURRENT_REQUESTS=1` уже стоит, а при проблемах переключитесь
  на Brave/Tavily.
- Ссылки в ответе зависят от модели: Pollinations иногда игнорирует
  инструкцию цитировать источники. Платные модели следуют лучше.
