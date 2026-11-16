# VPN: зарубежный трафик через прокси

Pi стоит в сети SkyNet, где заблокирован Telegram и часть зарубежных сервисов.
VPN-подписка (VLESS) уже в `.env` (`VPN_KEY`), а `Xray-core` поднимает из неё
локальный HTTP/SOCKS-прокси. Контейнеры, которым нужен зарубежный доступ,
ходят через него; локальные сервисы и обновления — напрямую.

## Схема

```
telegram-bot ─┐
proactive ────┼─ HTTPS_PROXY=http://vpn:2080 ─→ omni-vpn (Xray)
              │                                   │ VLESS (ws+tls)
              │                                   ▼
        open-webui, postgres, qdrant         VPN-сервер (Auto)
        (NO_PROXY — напрямую)
```

Прокси слушает два порта: **2080** — HTTP CONNECT (его используют контейнеры
через `HTTPS_PROXY`), **2081** — SOCKS5. Оба опубликованы только на
`127.0.0.1` для проверок с хоста, в сеть не выставлены.

Почему не TUN и не «весь трафик»:

- TUN в Docker требует `NET_ADMIN` и ломает DNS (провайдерский резолвер
  отравлен, а Docker DNS живёт вне netns).
- Прокси решает DNS на стороне VPN — обход блокировок по домену работает.
- `apt`, `docker pull` и прочие обновления хоста идут напрямую, как вы просили:
  VPN не тратит трафик на большие загрузки.

## Выбор сервера

`scripts/vpn-config.py`:

1. Скачивает подписку из `VPN_KEY` (base64 → список `vless://`).
2. Оставляет только **обычные Auto-серверы**.
3. Исключает: `🤖 AI` (нейросети), `📃 Белые списки`, торренты, YouTube,
   Госуслуги.
4. Поддерживает ws, xhttp, grpc, tcp: провайдер отдаёт VLESS поверх
   xhttp+reality, поэтому клиент — Xray-core (sing-box xhttp не умеет).
   Для xhttp принудительно ставится `mode: auto`: провайдерский
   `stream-one` рвёт TLS при потоке коротких соединений.
5. Замеряет TCP-задержку и берёт **до 6 самых быстрых** в пул.
6. Серверы объединяются балансером `leastPing` с `observatory`:
   Xray проверяет их каждые 3 минуты и уводит трафик с мёртвого.
7. Если все Auto недоступны, берутся обычные серверы (без AI, белых
   списков, торрентов и YouTube) — иначе Telegram останется без связи.

Результат — `${DATA_ROOT}/vpn/config.json` (права 600, в git не попадает).

```bash
make vpn-config            # перегенерировать (выберет лучший Auto)
python3 scripts/vpn-config.py --list          # показать кандидатов
VPN_SERVER="Auto 12 → [🚀 Оптимальная]" python3 scripts/vpn-config.py  # вручную
```

После смены сервера: `make vpn-up` (пересоздаёт контейнер с новым конфигом).

## Кто ходит через VPN

| Сервис | Причина |
|---|---|
| `telegram-bot` | `api.telegram.org` заблокирован |
| `proactive` | Telegram + внешние API брифинга |

Добавить ещё сервис — две строки в `docker-compose.yml`:

```yaml
    environment:
      HTTPS_PROXY: http://vpn:2080
      HTTP_PROXY: http://vpn:2080
      NO_PROXY: open-webui,postgres,qdrant,redis,vikunja,vpn,localhost,127.0.0.1
```

Важно: `NO_PROXY` обязателен для локальных имён, иначе приложение пойдёт
к `postgres`/`qdrant` через VPN и не найдёт их. Работает только для клиентов,
которые уважают переменные прокси (requests, httpx, urllib, git, curl).
Бинарные протоколы (например, SQL) через такой прокси не ходят — и не должны.

## Мониторинг трафика

```bash
make vpn-traffic          # итоги с момента старта контейнера
make vpn-watch            # скорость каждые 5 секунд
./scripts/vpn-traffic.sh --log   # точка в журнал
```

Журнал: `${DATA_ROOT}/logs/vpn-traffic.log` (`timestamp rx_bytes tx_bytes`).
Для ежечасных точек — cron:

```cron
7 * * * * /opt/omnisecretary/scripts/vpn-traffic.sh --log >> /var/log/omni-vpn-traffic.log 2>&1
```

Счётчики — сетевой интерфейс контейнера `omni-vpn`, то есть ровно тот трафик,
который ушёл в VPN (включая служебный TLS-оверхед).

Проверка, что прокси живой и выходной IP не домашний:

```bash
make vpn-test
```

## Диагностика

| Симптом | Что смотреть |
|---|---|
| `make vpn-test` пусто | `make vpn-logs`; скорее всего выбран xhttp-сервер — перегенерируйте конфиг |
| Telegram `401 Unauthorized` | неверный токен бота (не сеть) — перевыпустите у @BotFather |
| Приложение не видит `postgres`/`qdrant` | добавьте их в `NO_PROXY` |
| Медленно | `VPN_SERVER=...` и другой Auto, затем `make vpn-up` |
| Прокси работает, бот молчит | `docker logs omni-telegram-bot`; проверьте `TELEGRAM_API_BASE` |
| `sendMessage` → `400 chat not found` | получатель ещё не нажал **Start** у бота — это не ошибка сети |

## Безопасность

- `VPN_KEY` — секрет: только `.env`, в git не попадает.
- `config.json` содержит UUID/ключи VLESS — права 600, каталог 700.
- Прокси не публикуется наружу (`127.0.0.1:2080`).
- VPN-провайдер видит ваш трафик — это доверие к провайдеру, не к сети.
