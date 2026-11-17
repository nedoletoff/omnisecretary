# Rootless Docker: состояние и план переезда

Rootless-демон уже установлен и проверен, но **рабочий стек пока остаётся на
rootful Docker**. Это осознанно: переезд требует остановки сервисов и
переноса томов, а сервис нужен каждый день. Ниже — что сделано, что
проверено и как переехать без потери данных.

## Что уже сделано

```bash
uidmap                                   # установлен (newuidmap/newgidmap)
dockerd-rootless-setuptool.sh install    # демон для пользователя nedoletoff
loginctl enable-linger nedoletoff        # стартует без входа в сессию
sysctl net.ipv4.ip_unprivileged_port_start=80
```

Проверка rootless-демона:

```bash
XDG_RUNTIME_DIR=/run/user/1000 docker context use rootless
docker info --format '{{.SecurityOptions}} {{.DockerRootDir}}'
# [name=rootless ...] /home/nedoletoff/.local/share/docker
docker run --rm --network none alpine:3.22 echo "rootless ok"
```

Рабочий контекст возвращён на `default` (rootful), чтобы `docker`/`make`
продолжали работать как раньше. Rootless доступен явно:

```bash
docker context use rootless     # переключиться
docker context use default      # вернуться
```

## Почему не переключили сразу

| Риск | Что значит |
|---|---|
| Тома | данные postgres/qdrant/openwebui принадлежат root и uid 999; в rootless их нужно переносить и менять владельца на subuid (100999) |
| Порты 80/443 | rootless может их слушать только при `ip_unprivileged_port_start=80` (уже включено) |
| Сертификаты | `/etc/letsencrypt` читается rootless-демоном только если файлы доступны пользователю |
| Простой | перенос ~22 ГБ томов и проверка всех 20 контейнеров — 30–60 минут |
| Откат | нужен старый data-root и compose до переезда |

## План переезда (окно 1 час)

```bash
# 1. Бэкап и стоп
make backup
sudo docker compose down

# 2. Копия data-root в домашний каталог пользователя
sudo rsync -aH --info=progress2 /srv/omnisecretary/docker/ \
     /home/nedoletoff/.local/share/docker-rootless/

# 3. Владельцы томов: контейнерный root → subuid
#    uid 999 (postgres) → 100999, остальное — 100000:100000
sudo chown -R 100000:100000 /home/nedoletoff/.local/share/docker-rootless
sudo chown -R 100999:100999 /home/nedoletoff/.local/share/docker-rootless/volumes/postgres_data

# 4. Конфиг демона
mkdir -p ~/.config/docker
cat > ~/.config/docker/daemon.json <<'JSON'
{ "data-root": "/home/nedoletoff/.local/share/docker-rootless" }
JSON
systemctl --user restart docker

# 5. Запуск стека в rootless-контексте
docker context use rootless
cd /opt/omnisecretary && docker compose up -d

# 6. Проверка
./scripts/smoke-test.sh
```

Если что-то не поднялось — откат:

```bash
docker context use default
cd /opt/omnisecretary && sudo docker compose up -d
```

Данные в старом data-root остаются нетронутыми до удачного smoke-теста.

## Что учесть в compose

- `ports: "80:80"` работает при `ip_unprivileged_port_start=80`;
- `cap_drop: ALL` в rootless почти всегда ок, но `DAC_OVERRIDE` может не
  понадобиться;
- монтирование `/etc/letsencrypt` должно быть доступно пользователю
  (rootless-демон не может читать root-only файлы);
- healthcheck'и и `restart: unless-stopped` работают как обычно.

## Когда переезжать

После того как вы закончите отладку функций и будет свободный час.
Команда для проверки готовности: `make smoke` — 48 проверок должны быть
зелёными и на rootful, и после переезда.
