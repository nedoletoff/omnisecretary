"""OpenAI-совместимый STT-эндпоинт: sherpa-onnx + русская модель Vosk.

Open WebUI с AUDIO_STT_ENGINE=openai шлёт multipart на
/v1/audio/transcriptions и ждёт {"text": "..."}.

Почему sherpa-onnx, а не vosk-python: alphacephei.com (единственный источник
Kaldi-моделей) недоступен из сети Pi, а на HuggingFace лежит та же модель
Vosk (Zipformer2, WER 9.8 на Common Voice ru) в ONNX-формате. sherpa-onnx —
onnxruntime без Kaldi: легче и быстрее на ARM, качество то же.

Модель offline: декодируем запись целиком. Для голосового ввода в чат этого
достаточно, а streaming-вариант Vosk (597 МБ) в 4 ГБ ОЗУ не влезает.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import numpy as np
import sherpa_onnx
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse

MODEL_DIR = Path(os.environ.get("STT_MODEL_DIR", "/models/vosk"))
SAMPLE_RATE = 16000
MAX_BYTES = int(os.environ.get("STT_MAX_BYTES", str(25 * 1024 * 1024)))
NUM_THREADS = int(os.environ.get("STT_THREADS", "2"))

app = FastAPI(title="OmniSecretary STT", version="2.0")
_recognizer = None


def _model_file(name: str, quantized: str) -> Path:
    """int8-файл, если он есть, иначе обычный."""
    for candidate in (MODEL_DIR / "am" / quantized, MODEL_DIR / "am" / name):
        if candidate.is_file():
            return candidate
    raise HTTPException(500, f"нет файла модели: {MODEL_DIR / 'am' / name}")


def recognizer() -> "sherpa_onnx.OfflineRecognizer":
    global _recognizer
    if _recognizer is None:
        tokens = MODEL_DIR / "lang" / "tokens.txt"
        if not tokens.is_file():
            raise HTTPException(500, f"нет файла модели: {tokens}")
        _recognizer = sherpa_onnx.OfflineRecognizer.from_transducer(
            encoder=str(_model_file("encoder.onnx", "encoder.int8.onnx")),
            decoder=str(_model_file("decoder.onnx", "decoder.int8.onnx")),
            joiner=str(_model_file("joiner.onnx", "joiner.int8.onnx")),
            tokens=str(tokens),
            num_threads=NUM_THREADS,
            sample_rate=SAMPLE_RATE,
            feature_dim=80,
            decoding_method="greedy_search",
        )
    return _recognizer


@app.get("/healthz")
def healthz() -> JSONResponse:
    return JSONResponse({"status": "ok", "engine": "sherpa-onnx",
                         "model": MODEL_DIR.name, "language": "ru"})


@app.post("/v1/audio/transcriptions")
async def transcribe(file: UploadFile = File(...),
                     model_name: str = Form("vosk", alias="model"),
                     language: str = Form("")) -> JSONResponse:
    del model_name, language
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "пустой файл")
    if len(raw) > MAX_BYTES:
        raise HTTPException(413, "файл больше лимита")

    # Сырой PCM без заголовка: модель ждёт 16 кГц моно float32.
    process = subprocess.run(
        ["ffmpeg", "-nostdin", "-y", "-i", "pipe:0",
         "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "s16le",
         "-acodec", "pcm_s16le", "pipe:1"],
        input=raw, capture_output=True, timeout=180,
    )
    if process.returncode != 0 or not process.stdout:
        raise HTTPException(400, "не удалось декодировать аудио")

    samples = np.frombuffer(process.stdout, dtype="<i2").astype(np.float32) / 32768.0
    stream = recognizer().create_stream()
    stream.accept_waveform(SAMPLE_RATE, samples)
    recognizer().decode_stream(stream)
    text = stream.result.text.strip()
    return JSONResponse({"text": text})
