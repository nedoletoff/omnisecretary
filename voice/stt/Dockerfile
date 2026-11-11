# STT: sherpa-onnx + русская модель Vosk (ONNX) на Pi.
# Модель — та же, что vosk-model-small-ru, но в ONNX-формате с HuggingFace
# (alphacephei.com недоступен из сети Pi). ffmpeg приводит любой формат
# к 16 кГц моно PCM — то, что ждёт streaming-модель.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    STT_MODEL_DIR=/models/vosk \
    STT_THREADS=2

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
 && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
      "sherpa-onnx==2.0.1" \
      "numpy==2.3.4" \
      "fastapi==0.121.0" \
      "uvicorn[standard]==0.38.0" \
      "python-multipart==0.0.20" \
      "huggingface_hub==0.36.0"

# int8-энкодер (25 МБ) вместо fp32 (90 МБ): меньше ОЗУ и быстрее на ARM.
# Альтернативная (streaming) модель Vosk — 597 МБ, в 4 ГБ не влезает.
ARG STT_MODEL_REPO=alphacep/vosk-model-small-ru
RUN python -c "from huggingface_hub import snapshot_download; \
snapshot_download('${STT_MODEL_REPO}', local_dir='/models/vosk', \
allow_patterns=['am/encoder.int8.onnx','am/decoder.int8.onnx', \
'am/joiner.int8.onnx','am/decoder.onnx','am/joiner.onnx','lang/tokens.txt'])" \
 && test -f /models/vosk/am/encoder.int8.onnx

COPY app.py /app/app.py
WORKDIR /app

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/healthz',timeout=3)"

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
