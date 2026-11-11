"""OpenAI-совместимый TTS-эндпоинт: Silero (основной) или Piper (лёгкий).

Open WebUI с AUDIO_TTS_ENGINE=openai шлёт POST /v1/audio/speech
{model, input, voice} и ждёт аудиофайл.

Silero v4_ru — 40 МБ модель + torch; на Pi 4 даёт естественную русскую речь
с ударениями. Piper — onnxruntime, ~60 МБ ОЗУ, быстрее, но голос проще.
Выбор — переменной TTS_BACKEND.
"""

from __future__ import annotations

import io
import os
import wave
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

BACKEND = os.environ.get("TTS_BACKEND", "silero").lower()
SILERO_MODEL = os.environ.get("SILERO_MODEL_PATH", "/models/v4_ru.pt")
SILERO_SPEAKERS = ("aidar", "baya", "kseniya", "xenia", "eugene")
DEFAULT_VOICE = os.environ.get("TTS_DEFAULT_VOICE", "baya")
SAMPLE_RATE = int(os.environ.get("TTS_SAMPLE_RATE", "48000"))
MAX_CHARS = int(os.environ.get("TTS_MAX_CHARS", "1000"))
PIPER_MODEL = os.environ.get("PIPER_MODEL_PATH", "/models/ru_RU-irina-medium.onnx")

app = FastAPI(title="OmniSecretary TTS", version="1.0")
_silero = None
_piper = None


class SpeechRequest(BaseModel):
    model: str = "tts"
    input: str
    voice: str = ""
    response_format: str = "wav"


def _pcm_to_wav(samples, sample_rate: int) -> bytes:
    """int16-сэмплы → WAV в памяти (без torchaudio)."""
    import numpy as np

    if hasattr(samples, "detach"):
        samples = samples.detach().cpu().numpy()
    array = np.asarray(samples, dtype=np.float32)
    pcm = (array.clip(-1.0, 1.0) * 32767).astype("<i2").tobytes()
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(pcm)
    return buffer.getvalue()


def _load_silero():
    global _silero
    if _silero is None:
        import torch

        if not Path(SILERO_MODEL).is_file():
            raise HTTPException(500, f"модель Silero не найдена: {SILERO_MODEL}")
        # v4_ru.pt — это torch.package (zip), а не TorchScript-архив.
        package = torch.package.PackageImporter(str(SILERO_MODEL))
        model = package.load_pickle("tts_models", "model")
        model.to("cpu")
        _silero = model
    return _silero


def _load_piper():
    global _piper
    if _piper is None:
        from piper import PiperVoice

        if not Path(PIPER_MODEL).is_file():
            raise HTTPException(500, f"голос Piper не найден: {PIPER_MODEL}")
        _piper = PiperVoice.load(PIPER_MODEL)
    return _piper


@app.get("/healthz")
def healthz() -> JSONResponse:
    return JSONResponse({"status": "ok", "backend": BACKEND,
                         "voice": DEFAULT_VOICE})


@app.get("/v1/audio/voices")
def voices() -> JSONResponse:
    if BACKEND == "piper":
        return JSONResponse({"voices": [Path(PIPER_MODEL).stem]})
    return JSONResponse({"voices": list(SILERO_SPEAKERS)})


@app.post("/v1/audio/speech")
def speech(request: SpeechRequest) -> Response:
    text = (request.input or "").strip()
    if not text:
        raise HTTPException(400, "пустой текст")
    if len(text) > MAX_CHARS:
        raise HTTPException(413, f"текст длиннее {MAX_CHARS} символов")

    if BACKEND == "piper":
        from piper import SynthesisConfig

        voice = _load_piper()
        config = SynthesisConfig()
        chunks = list(voice.synthesize(text, syn_config=config))
        if not chunks:
            raise HTTPException(500, "Piper не вернул аудио")
        sample_rate = chunks[0].sample_rate
        pcm = b"".join(chunk.audio_int16_bytes for chunk in chunks)
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(sample_rate)
            handle.writeframes(pcm)
        audio = buffer.getvalue()
    else:
        import torch

        model = _load_silero()
        speaker = request.voice if request.voice in SILERO_SPEAKERS else DEFAULT_VOICE
        with torch.no_grad():
            samples = model.apply_tts(
                text=text, speaker=speaker, sample_rate=SAMPLE_RATE,
                put_accent=True, put_yo=True,
            )
        audio = _pcm_to_wav(samples, SAMPLE_RATE)

    return Response(content=audio, media_type="audio/wav",
                    headers={"Content-Disposition": 'inline; filename="speech.wav"'})
