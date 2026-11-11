# Планировщик брифингов: stdlib + fastapi, без тяжёлых зависимостей.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    BRIEFING_TZ=Europe/Moscow \
    BRIEFING_TIME=07:30 \
    BRIEFING_STATE=/state/last-briefing \
    VAULT_DIR=/vault

RUN pip install --no-cache-dir \
      "fastapi==0.121.0" \
      "uvicorn[standard]==0.38.0"

COPY app.py /app/app.py
WORKDIR /app

EXPOSE 8000
HEALTHCHECK --interval=60s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/healthz',timeout=3)"

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
