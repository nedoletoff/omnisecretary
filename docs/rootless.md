# Docker Rootless

Статус: **миграция выполнена**, rootless — рабочий режим.

- Контекст: `rootless` (по умолчанию), демон: `systemd --user docker`,
  linger включён.
- Data-root: `/srv/omnisecretary/rootless/docker`, тома:
  `/srv/omnisecretary/rootless/volumes` (bind-монты).
- Rootful демон (`docker.service`, `docker.socket`) отключён, данные
  сохранены для отката.

## Текущее состояние

```
docker context show            # rootless
systemctl --user status docker
docker compose ps
make smoke                     # 48/48
```

Контейнеры поднимаются после перезагрузки (`restart: unless-stopped`,
linger + user-сервис). `sudo` для `docker` не требуется.

## Что учтено при миграции

- **Тома:** именованные тома переведены на bind-монты в
  `${VOLUMES_ROOT}` — в rootless `driver_opts: bind` для named volumes
  ненадёжен.
- **UID/GID (subuid/subgid):** владельцы файлов томов приведены к карте
  пользователя (uid 0 в контейнере → 1000 на хосте, 999 → 100998,
  1000 → 100999 и т.д.). Ошибка на единицу даёт permission denied.
- **Порты 80/443:** в rootless доступны; при проблемах — `setcap` или
  редирект, но в текущей конфигурации работают.
- **Сертификаты:** `/etc/letsencrypt` недоступен rootless-демону; копии в
  `/srv/omnisecretary/letsencrypt` и `nginx/certs/` (600),
  `scripts/sync-certs.sh`, certbot `v5.10.0`, продление из cron.
- **Бэкап Qdrant:** исправлен путь к снапшоту коллекции.

## Проверка

```bash
make ps
make smoke
curl -sk https://nedoletoff.sknt.ru/healthz
curl -sk https://nedoletoff.mooo.com/healthz
```

## Переключение контекста

```bash
docker context use rootless   # рабочий
docker context use default    # rootful (откат, если включён)
```

## Откат на rootful (при необходимости)

1. Остановить rootless-стек: `docker --context rootless compose down`.
2. Включить rootful: `sudo systemctl enable --now docker`.
3. `docker context use default`.
4. Вернуть/синхронизировать данные томов и сертификатов, поднять стек.
5. Убедиться `make smoke`.

Данные rootful сохранялись отдельно; перед любым переключением — бэкап.

## См. также

- [operations.md](operations.md) — бэкапы, перенос, обслуживание
- [decisions.md](decisions.md) — ADR-021, ADR-018
- [security.md](security.md) — модель угроз и изоляция
