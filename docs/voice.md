# Голос: русский STT и TTS

Голос — два отдельных лёгких контейнера. Ядро (Open WebUI) обращается к ним
по OpenAI-совместимым эндпоинтам, поэтому локальные модели не занимают
память ядра и не мешают обновлению Open WebUI.

```
Микрофон (браузер/PWA)
   │  webm/ogg
   ▼
open-webui ──▶ stt:8000/v1/audio/transcriptions ──▶ sherpa-onnx (Vosk small-ru)
   │                                                     │
   │  текст                                              ▼
   ▼                                              ответ LLM
open-webui ──▶ tts:8000/v1/audio/speech ──▶ Silero v4_ru (torch) ──▶ WAV
```

## Запуск

```bash
# STT + Silero TTS
make voice-up

# или лёгкая альтернатива Piper вместо Silero
make voice-piper

make smoke
```

Профили compose: `voice` (stt + tts), `voice-piper` (tts-piper).
Одновременно Silero и Piper не нужны: Open WebUI указывает на один адрес.

Переменные в `.env`:

```dotenv
TTS_DEFAULT_VOICE=baya          # aidar, baya, kseniya, xenia, eugene
AUDIO_TTS_OPENAI_API_BASE_URL=http://tts:8000/v1   # для Piper: http://tts-piper:8000/v1
```

## STT: sherpa-onnx + модель Vosk

| Параметр | Значение |
|---|---|
| Модель | `alphacep/vosk-model-small-ru` (Zipformer2, WER 9.8 на Common Voice ru) |
| Движок | sherpa-onnx 2.0.1 (onnxruntime, без Kaldi) |
| Размер | encoder int8 25 МБ + decoder/joiner ~1,5 МБ |
| Память | ~180–220 МБ в работе |
| Порт | 8000 внутри `backend`, наружу не публикуется |

**Почему не `vosk` python:** единственный источник Kaldi-моделей
(`alphacephei.com`) недоступен из сети Pi (DNS отдаёт только IPv6, маршрута
нет). На HuggingFace лежит та же модель Vosk в ONNX-формате — sherpa-onnx
работает с ней напрямую, качество идентично, зависимость легче.

**Почему не streaming-модель:** `vosk-model-small-streaming-ru` — 597 МБ,
на Pi 4 с 4 ГБ ОЗУ это нерационально. Offline-модель распознаёт запись
целиком: 7 секунд речи — около 10 секунд на Pi 4 (int8, 4 потока).

Замер: `test.wav` из репозитория модели → «родион потапыч высчитывал каждый
новый вершок углубления и давно определил про себя» — верно, 10,3 с.

## TTS: Silero v4_ru (основной)

| Параметр | Значение |
|---|---|
| Модель | `v4_ru.pt` (40 МБ), TorchScript |
| Движок | torch 2.9.1 CPU (aarch64 wheel, без CUDA) |
| Голоса | `aidar`, `baya` (по умолчанию), `kseniya`, `xenia`, `eugene` |
| Частота | 48 кГц |
| Память | ~350–500 МБ в работе |
| Формат ответа | WAV (`audio/wav`) |

Silero ставит ударения (`put_accent`) и «ё» (`put_yo`), поэтому русская речь
звучит естественно. Это самый «тяжёлый» сервис стека — 640 МБ лимита.

Замер на Pi 4: первый синтез 18,6 с (загрузка модели), далее 0,5–8 с в
зависимости от длины фразы; 3,3 с речи — WAV 48 кГц, 320 КБ, пик 32767
(сигнал не пустой).

## TTS: Piper (альтернатива)

| Параметр | Значение |
|---|---|
| Голос | `ru_RU-irina-medium` (ONNX + json) |
| Движок | piper-tts 1.3.0 (onnxruntime) |
| Память | ~60–90 МБ |
| Скорость | быстрее Silero, голос проще |

```bash
make voice-piper
# затем в .env:
AUDIO_TTS_OPENAI_API_BASE_URL=http://tts-piper:8000/v1
make restart
```

## API

```bash
# Распознавание (multipart, поле file)
curl -F file=@voice.ogg http://stt:8000/v1/audio/transcriptions

# Озвучка
curl -X POST http://tts:8000/v1/audio/speech \
  -H 'Content-Type: application/json' \
  -d '{"input":"Доброе утро, хозяин","voice":"baya"}' -o speech.wav

# Список голосов и здоровье
curl http://tts:8000/v1/audio/voices
curl http://stt:8000/healthz
```

Порты не публикуются: обращаться изнутри сети `backend`
(`docker exec omni-open-webui curl ...` или `docker run --network ...`).

## Настройки Open WebUI

Уже прописаны в `docker-compose.yml`:

```dotenv
AUDIO_STT_ENGINE=openai
AUDIO_STT_OPENAI_API_BASE_URL=http://stt:8000/v1
AUDIO_STT_OPENAI_API_KEY=local
AUDIO_TTS_ENGINE=openai
AUDIO_TTS_OPENAI_API_BASE_URL=http://tts:8000/v1
AUDIO_TTS_OPENAI_API_KEY=local
AUDIO_TTS_MODEL=tts
AUDIO_TTS_VOICE=baya
```

Если голосовые контейнеры не запущены, микрофон и озвучка вернут ошибку —
остальной интерфейс работает. Включайте профиль `voice` на постоянной основе,
если голос нужен всегда.

## Память и ограничения

- STT 320 МБ + TTS 640 МБ (лимиты). Фактически ~200 + ~400 МБ.
- При включённом голосе сумма лимитов ~4,1 ГБ — выше 4 ГБ ОЗУ. Лимиты —
  потолок, не резервация; при нехватке памяти отключайте Piper/Silero
  или используйте только STT.
- memory-cgroup на Pi включается параметром ядра (см. ADR-018); без него
  `mem_limit` игнорируется и защита от OOM слабее.
- Голосовой режим доступен в веб-интерфейсе и PWA (этап 6), в Telegram —
  только текст.

## Диагностика

```bash
make voice-logs                 # логи stt и tts
docker exec omni-stt python -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8000/healthz').read())"
docker exec omni-tts python -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8000/healthz').read())"
```

| Симптом | Причина |
|---|---|
| 500 «не удалось декодировать аудио» | ffmpeg не понял формат; проверить `ffmpeg -i` |
| 413 | файл больше 25 МБ (`STT_MAX_BYTES`) |
| TTS 500 «модель Silero не найдена» | образ собран без модели — пересобрать |
| Микрофон в UI не работает | профиль `voice` не запущен |
| Медленное распознавание | `STT_THREADS` < 4 или параллельная нагрузка |
