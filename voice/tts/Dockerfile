# Silero TTS v4_ru — основной голос (русский, ударения).
# torch на arm64 ставится из PyPI; модель 40 МБ скачивается при сборке.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TTS_BACKEND=silero \
    SILERO_MODEL_PATH=/models/v4_ru.pt \
    TTS_DEFAULT_VOICE=baya \
    TTS_SAMPLE_RATE=48000

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates libgomp1 \
 && rm -rf /var/lib/apt/lists/*

# torch с PyPI: для aarch64 это CPU-сборка без CUDA (104 МБ wheel).
RUN pip install --no-cache-dir \
      "torch==2.9.1" \
 && pip install --no-cache-dir \
      "numpy==2.3.4" \
      "fastapi==0.121.0" \
      "uvicorn[standard]==0.38.0"

RUN mkdir -p /models \
 && curl -fsSL https://models.silero.ai/models/tts/ru/v4_ru.pt -o /models/v4_ru.pt \
 && test -s /models/v4_ru.pt

COPY app.py /app/app.py
WORKDIR /app

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/healthz',timeout=3)"

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
