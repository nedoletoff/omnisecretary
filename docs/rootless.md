# Rootless Docker: выполнено

Стек работает на **rootless** Docker. Rootful-демон отключён
(`systemctl disable --now docker.service docker.socket`), данные и образы
rootful сохранены — это точка отката.

## Как устроено

```
systemd --user docker.service (пользователь nedoletoff, linger включён)
  data-root: /srv/omnisecretary/rootless/docker
  тома:      /srv/omnisecretary/rootless/volumes
  порты 80/443: разрешены через net.ipv4.ip_unprivileged_port_start=80
```

Клиент `docker` по умолчанию использует контекст `rootless`:

```bash
docker context ls          # * rootless
docker info --format '{{.DockerRootDir}} {{.SecurityOptions}}'
```

`make`, `scripts/*.sh` и cron работают без `sudo`.

## Отображение пользователей (важно при переносе томов)

Rootless-демон использует subuid-диапазон `/etc/subuid`:

| uid в контейнере | владелец на хосте | Кто это |
|---|---|---|
| 0 | 1000 (`nedoletoff`) | большинство образов |
| 999 | 100998 | postgres, redis |
| 1000 | 100999 | vikunja |

Если том переносится или создаётся заново, владельца нужно ставить по этой
таблице, иначе контейнер получит `Permission denied`:

```bash
sudo chown -R 1000:1000  /srv/omnisecretary/rootless/volumes
sudo chown -R 100998:100998 /srv/omnisecretary/rootless/volumes/postgres \
                             /srv/omnisecretary/rootless/volumes/redis
sudo chown -R 100999:100999 /srv/omnisecretary/rootless/volumes/vikunja
sudo chmod 700 /srv/omnisecretary/rootless/volumes/postgres
```

## Сертификаты

Rootless-демон не читает `/etc/letsencrypt` (каталог root-only). Поэтому:

1. сертификаты живут в `/srv/omnisecretary/letsencrypt` (владелец —
   `nedoletoff`), туда пишет certbot в контейнере;
2. `scripts/sync-certs.sh` копирует `fullchain.pem`/`privkey.pem` в
   `nginx/certs/<домен>/` (права 600, владелец — пользователь);
3. nginx читает их из `/etc/nginx/certs/...`.

Продление: `make renew-certs` (cron `/etc/cron.d/omnisecretary-renew`,
ежедневно в 03:17 от пользователя `nedoletoff`).

## Секреты и доступные каталоги

| Каталог | Владелец | Кто пишет |
|---|---|---|
| `secrets/` | `nedoletoff`, ACL на чтение для контейнеров | скрипты, вручную |
| `vault/`, `vault-index/`, `bot/`, `portal/`, `status/`, `shared/` | `nedoletoff` | контейнеры (uid 0 → 1000) |
| `rootless/volumes/*` | по таблице выше | контейнеры |

`docker.sock` в контейнеры не монтируется, `cap_drop: ALL` сохранён.

## Проверка после перезагрузки

```bash
systemctl --user is-active docker     # active
docker ps --format '{{.Names}}'       # 20 контейнеров
make smoke                            # 48/48
```

Linger включён (`loginctl enable-linger nedoletoff`), поэтому стек
поднимается без входа в систему.

## Откат на rootful (если понадобится)

```bash
sudo systemctl enable --now docker.service docker.socket
docker context use default
cd /opt/omnisecretary
sudo docker compose --profile '*' up -d
```

Старые тома в `/srv/omnisecretary/volumes` не удалялись и не изменялись
после копирования, поэтому откат возвращает состояние на момент миграции.
Учтите: изменения, сделанные после переезда, останутся только в
`rootless/volumes` — перед откатом скопируйте их обратно.
