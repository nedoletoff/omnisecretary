# Эксплуатация

Ежедневные операции, бэкапы, переезд на внешний диск, мониторинг.

## Ежедневно

```bash
make ps          # все ли контейнеры живы
make smoke       # 33 проверки
make logs        # логи всех сервисов (Ctrl+C)
```

## Бэкапы

```bash
make backup
```

Создаёт в `${DATA_ROOT}/backups/<дата>/`:

| Файл | Что |
|---|---|
| `postgres.sql.gz` | дамп обеих БД (openwebui + vikunja) |
| `qdrant.snapshot` | снапшот коллекций Qdrant |
| `openwebui/` | `webui.db`, `config.json`, загруженные файлы |
| `vault.bundle` | git-bundle Obsidian-вольта (если подключён) |
| `SHA256SUMS` | контрольные суммы |

`.env` в бэкап не попадает: секреты храните отдельно (менеджер паролей).
Ротация: хранится 7 последних каталогов.

### Восстановление

```bash
# 1. Остановить сервисы, которые пишут в БД
docker compose stop open-webui vikunja

# 2. Postgres
gunzip -c backups/<дата>/postgres.sql.gz | \
  docker compose exec -T postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"

# 3. Qdrant: скопировать снапшот и восстановить через API
docker compose start qdrant
curl -X POST "http://127.0.0.1:6333/collections/<имя>/snapshots/upload?priority=snapshot" \
  -F "snapshot=@backups/<дата>/qdrant.snapshot"

# 4. Вольт
git clone backups/<дата>/vault.bundle /srv/omnisecretary/vault

# 5. Поднять всё
docker compose up -d && make smoke
```

## Переезд на внешний диск

Сейчас `${DATA_ROOT}=/srv/omnisecretary` (SD-карта). Когда появится
внешний SSD/HDD:

```bash
# 1. Разметить и смонтировать диск (вручную, осознанно)
sudo mkdir -p /mnt/data
sudo mount /dev/sdX1 /mnt/data
# прописать в /etc/fstab по UUID (blkid), затем проверить: sudo mount -a

# 2. Остановить стек
cd /opt/omnisecretary && make down

# 3. Перенести данные (rsync сохраняет права и владельцев)
sudo rsync -aHAX --info=progress2 /srv/omnisecretary/ /mnt/data/omnisecretary/

# 4. Поменять путь в .env
#    DATA_ROOT=/mnt/data/omnisecretary
#    (DOCKER_ROOT уже указывает на /mnt/data/docker — см. bootstrap)

# 5. Поднять и проверить
make up && make smoke
```

После переезда старый каталог не удаляйте, пока не пройдёт smoke и не
убедитесь, что чаты и заметки на месте.

## Мониторинг

`scripts/health-alert.sh` прогоняет smoke-тест и при ошибке пишет в Telegram.

Пример systemd-таймера (устанавливать вручную, с подтверждением):

```ini
# /etc/systemd/system/omnisecretary-health.service
[Unit]
Description=OmniSecretary health check

[Service]
Type=oneshot
User=nedoletoff
WorkingDirectory=/opt/omnisecretary
ExecStart=/opt/omnisecretary/scripts/health-alert.sh
```

```ini
# /etc/systemd/system/omnisecretary-health.timer
[Unit]
Description=OmniSecretary health check every 15 minutes

[Timer]
OnBootSec=5min
OnUnitActiveSec=15min

[Install]
WantedBy=timers.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now omnisecretary-health.timer
```

Лог: `${DATA_ROOT}/logs/health-alert.log`.

## Обновление

```bash
make update        # git pull + пересборка + smoke
```

Версии образов фиксированы в `docker-compose.yml`. Обновление версии —
отдельный коммит:

1. Проверить `linux/arm64` в манифесте нового тега:
   `docker manifest inspect <образ>:<тег> | grep arm64`.
2. Поменять тег, `make pull && make up`.
3. `make smoke`, прогнать чат/инструменты, затем коммит.

## Сертификаты

```bash
make renew-certs       # продлить и перезагрузить nginx
```

Сертификат `nedoletoff.sknt.ru` живёт в `/etc/letsencrypt/live/...` (смонтирован
в nginx read-only). Автопродление — cron:

```cron
17 3 * * * /opt/omnisecretary/scripts/renew-certs.sh >> /var/log/omni-certs.log 2>&1
```

## Логи и износ SD

- Docker: json-file, 10 МБ × 3 на контейнер (`logging` в compose).
- Приложение: `${DATA_ROOT}/logs`.
- Перенесите Docker data-root и `${DATA_ROOT}` на SSD — это главный способ
  продлить жизнь карте памяти.
- `sudo journalctl --vacuum-size=200M` раз в месяц (вручную).

## Напоминания в Telegram

В боте (после запуска профиля `telegram`):

```
/remind 20:30 позвонить маме
/remind +15m проверить духовку
/remind 12.11 09:00 встреча с врачом
/reminders
/forget 3
```

Напоминания хранятся в `bot.db` (SQLite, права 600), отправляются потоком
раз в 20 секунд. Если бот выключен в момент срока — напоминание уйдёт при
следующем запуске (просроченные не отбрасываются).

## Что делать, если

| Симптом | Действие |
|---|---|
| `502` от nginx | `make ps` — жив ли `open-webui`; `make logs` |
| чат не отвечает | проверить ключ провайдера в `.env`, `make restart` |
| MCP-инструмент «unknown tool» | `make mcp-logs`, пересобрать `make mcp-build` |
| вольт не синхронизируется | `http://<pi>/vault/` → «Синхронизировать», смотреть `make vault-logs` |
| мало памяти | `docker stats --no-stream`; отключить `voice`/`tts` |
| не приходит брифинг | `make briefing` — текст собирается? Проверить токен и `make proactive-logs` |
