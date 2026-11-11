# Общий образ для собственных MCP-серверов и vault-manager.
# Один образ на все сервисы: меньше места на SD и одинаковые версии.
FROM python:3.12-slim

# git нужен для коммитов и push вольта; ca-certificates — для HTTPS.
RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Код монтируется read-only (см. docker-compose.yml).
CMD ["python", "/app/servers/vault.py"]
