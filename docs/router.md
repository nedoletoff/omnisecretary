# Внешний доступ: роутер, DNS, HTTPS

Цель: открыть OmniSecretary из интернета на своём железе (SkyNet, белый IP,
порты 80/443 открыты у провайдера) и получить HTTPS для Telegram Mini App.

## Симптом

Снаружи открывается только веб-интерфейс роутера на `:8080`
(`nedoletoff.sknt.ru:8080`), виртуальные серверы для Pi не работают.

Причина почти всегда одна из трёх:

1. **Remote Management роутера занял 8080** и отвечает раньше проброса.
2. **WAN-IP роутера — не белый** (CGNAT у провайдера), тогда проброс
   бесполезен: пакеты не доходят до роутера.
3. Запись Virtual Server сохранена не для того интерфейса/протокола, либо
   Pi получил другой IP по DHCP.

## Шаг 1. Проверить WAN-IP и CGNAT

На Pi:

```bash
curl -4 -s https://api.ipify.org; echo
ip -4 addr show | grep 'inet ' | grep -v 127.0.0.1
```

В веб-интерфейсе роутера (Archer C5 v4): **Status → WAN** — сравните
«IP Address» с выводом `api.ipify.org`.

| WAN-IP | Вывод |
|---|---|
| совпадает с внешним | белый IP, проброс имеет смысл |
| `100.64.x.x` – `100.127.x.x` | CGNAT, звонить провайдеру |
| `10.x`, `172.16–31.x`, `192.168.x` | двойной NAT (роутер за роутером) |

Если CGNAT — попросите SkyNet выдать публичный IPv4 (у вас он заявлен) или
перейти на туннель (шаг 7).

## Шаг 2. Закрепить IP за Pi

1. Узнать MAC проводного интерфейса: `ip link show eth0`.
2. Роутер → **Network → DHCP → Address Reservation**: закрепить
   `192.168.0.133` за этим MAC.
3. Перезагрузить Pi и убедиться: `ip -4 addr show eth0`.

## Шаг 3. Virtual Servers (Archer C5 v4)

**Advanced → NAT Forwarding → Virtual Servers → Add**:

| Service Port (внешний) | Internal IP | Internal Port | Protocol |
|---|---|---|---|
| 80 | 192.168.0.133 | 80 | TCP |
| 443 | 192.168.0.133 | 443 | TCP |

Важно:

- Protocol — **TCP**, не «ALL»: некоторые прошивки C5 с «ALL» не создают
  правило.
- После добавления нажать **Save**, затем перезагрузить роутер
  (**System Tools → Reboot**) — правила применяются не всегда сразу.
- Не добавлять 8081, 5432, 6333: наружу должны смотреть только 80/443.

## Шаг 4. Отключить Remote Management на 8080

Именно он отвечает снаружи вместо Pi.

**System Tools → Administration → Remote Management**:

- выключить полностью, **или**
- сменить порт на нестандартный и ограничить список IP.

После этого `http://nedoletoff.sknt.ru:8080` перестанет открывать роутер.
Это ожидаемо и правильно.

## Шаг 5. Проверить проброс снаружи

С телефона в мобильной сети (не Wi-Fi):

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://nedoletoff.sknt.ru/healthz
```

- `200` — проброс работает;
- таймаут — правило не применилось или CGNAT;
- открывается роутер — Remote Management всё ещё включён.

Проверка «изнутри» через внешний IP часто не работает (hairpin NAT на C5
не поддерживается) — это нормально, тестируйте с мобильного интернета.

## Шаг 6. DNS

У регистратора домена `sknt.ru` (или в панели SkyNet) должна быть A-запись:

```
nedoletoff.sknt.ru.  A  <белый IP>
```

Проверка:

```bash
dig +short nedoletoff.sknt.ru
```

Должен совпасть с WAN-IP. Если домен делегирован SkyNet — запись меняется
в личном кабинете провайдера.

## Шаг 7. HTTPS — уже настроено

Сертификат Let's Encrypt выпущен 12.11.2026 для `nedoletoff.sknt.ru`
(certbot в Docker, webroot `nginx/www`). HTTPS работает снаружи:

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://nedoletoff.sknt.ru/healthz   # 200
```

Конфиг — `nginx/conf.d/10-ssl.conf`, сертификаты — `/etc/letsencrypt`,
смонтированы в контейнер `omni-nginx` read-only.

Продление (сертификат до 10.02.2027):

```bash
make renew-certs          # certbot renew + reload nginx
```

Автопродление — cron (установлен в `/etc/cron.d/omnisecretary-renew`, лог `/srv/omnisecretary/logs/renew-certs.log`):

```cron
17 3 * * * /opt/omnisecretary/scripts/renew-certs.sh >> /var/log/omni-certs.log 2>&1
```

Проверка: `sudo certbot certificates` (или `ls /etc/letsencrypt/live`).

### Telegram Mini App

Mini App открывается только по **HTTPS**. После сертификата:

```bash
# .env
PUBLIC_URL=https://nedoletoff.sknt.ru
```

`make tg-restart`, затем в боте команда `/app` — кнопка откроет веб-интерфейс
внутри Telegram с той же темой Gruvbox. Пока `PUBLIC_URL` начинается с
`http://`, бот откроет обычную ссылку в браузере.

## Шаг 8. Если провайдер всё же режет входящие

SkyNet заявляет открытые 80/443, но если шаг 5 стабильно даёт таймаут —
используйте Cloudflare Tunnel (исходящее соединение, входящие порты не нужны).
`cloudflared` уже установлен на Pi:

```bash
cloudflared tunnel login
cloudflared tunnel create omnisecretary
cloudflared tunnel route dns omnisecretary secretary.example.com
cloudflared tunnel run --url http://127.0.0.1:80 omnisecretary
```

Токен/сертификат туннеля — секрет, хранить в `secrets/`, не в git.
`PUBLIC_URL=https://secretary.example.com`.

## Что не делать

- Не включать DMZ на Pi: откроется весь Pi, включая Postgres и Qdrant.
- Не пробрасывать 8081 (Vikunja) и 6333 (Qdrant) наружу.
- Не хранить пароль роутера в репозитории.
- Не менять `/etc/fstab` и сетевые настройки Pi без бэкапа.

## Диагностика

| Симптом | Причина | Действие |
|---|---|---|
| снаружи `:8080` — роутер | Remote Management | шаг 4 |
| `502 Bad Gateway` | Pi недоступен/nginx упал | `make ps`, `make logs` |
| таймаут с мобильного | CGNAT или правило не сохранено | шаги 1–3 |
| `403` от nginx | Basic Auth вольта/ACL | проверить `nginx/conf.d` |
| Mini App не открывается | нет HTTPS | шаг 7 |
| сертификат не выпускается | 80 не доходит до Pi | шаг 5 |
