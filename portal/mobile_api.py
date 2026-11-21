"""Мобильный API OmniSecretary.

Клиент приложения ходит сюда с Bearer-токеном Open WebUI (тем же, что
получает при входе). Портал проверяет токен в Open WebUI и работает от
имени пользователя: чужие чаты, календари и заметки недоступны.

Зачем отдельный слой, если есть API Open WebUI: голос, агрегат «Сегодня»,
запись в вольт и напоминания в Open WebUI отсутствуют или требуют
админских прав.
"""

from __future__ import annotations

import asyncio
import base64
import wave
import io
import os
import json
import os
import sqlite3
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from fastapi import APIRouter, File, HTTPException, Request, UploadFile, Form
from fastapi.responses import JSONResponse, Response

import vaults as user_vaults

OWUI_URL = os.environ.get("OWUI_URL", "http://open-webui:8080").rstrip("/")
SECRETS_DIR = Path(os.environ.get("SECRETS_DIR", "/secrets"))
BOT_DB_PATH = Path(os.environ.get("BOT_DB_PATH", "/bot/bot.db"))
STATUS_FILE = Path(os.environ.get("STATUS_FILE", "/status/device.json"))
# Облачные OpenAI-совместимые голосовые API (опционально).
CLOUD_STT_URL = os.environ.get("CLOUD_STT_URL", "")
CLOUD_STT_KEY = os.environ.get("CLOUD_STT_KEY", "")
CLOUD_STT_MODEL = os.environ.get("CLOUD_STT_MODEL", "whisper-1")
CLOUD_TTS_URL = os.environ.get("CLOUD_TTS_URL", "")
CLOUD_TTS_KEY = os.environ.get("CLOUD_TTS_KEY", "")
CLOUD_TTS_MODEL = os.environ.get("CLOUD_TTS_MODEL", "gpt-4o-mini-tts")
STT_URL = os.environ.get("STT_URL", "http://stt:8000").rstrip("/")
TTS_URL = os.environ.get("TTS_URL", "http://tts:8000").rstrip("/")
VAULT_URL = os.environ.get("VAULT_MANAGER_URL", "http://vault-manager:8000").rstrip("/")
VIKUNJA_URL = os.environ.get("VIKUNJA_BASE_URL", "http://vikunja:3456").rstrip("/")
VIKUNJA_TOKEN = os.environ.get("VIKUNJA_API_TOKEN", "")
OWNER = os.environ.get("PORTAL_OWNER", "").strip()
DEFAULT_CITY = os.environ.get("WEATHER_CITY", "Санкт-Петербург, Ломоносовская")
TELEGRAM_OWNER_ID = os.environ.get("TELEGRAM_OWNER_ID", "").strip()
WEATHER_UA = "OmniSecretary/1.0 (self-hosted)"
DEFAULT_COORDS = (59.8772, 30.4413)

router = APIRouter(prefix="/api/m", tags=["mobile"])

_user_cache: dict[str, tuple[float, dict]] = {}


def _token(request: Request) -> str:
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        raise HTTPException(401, "нужен Bearer-токен")
    return header.split(" ", 1)[1].strip()


def _user(token: str) -> dict:
    """Проверяем токен в Open WebUI и возвращаем пользователя (кеш 60 с)."""
    now = time.time()
    cached = _user_cache.get(token)
    if cached and cached[0] > now:
        return cached[1]
    with httpx.Client(timeout=15) as client:
        response = client.get(f"{OWUI_URL}/api/v1/auths/",
                              headers={"Authorization": f"Bearer {token}"})
    if response.status_code != 200:
        raise HTTPException(401, "токен недействителен")
    user = response.json()
    _user_cache[token] = (now + 60, user)
    return user


def _is_owner(user: dict) -> bool:
    email = str(user.get("email", ""))
    local = email.split("@", 1)[0]
    return bool(OWNER) and local == OWNER


def _owui(token: str, path: str, params: dict | None = None) -> object:
    with httpx.Client(timeout=20) as client:
        response = client.get(f"{OWUI_URL}{path}", params=params,
                              headers={"Authorization": f"Bearer {token}"})
    if response.status_code != 200:
        raise HTTPException(response.status_code, f"Open WebUI: {path}")
    return response.json()


def _owui_json(method: str, path: str, token: str, body: dict | None = None,
               params: dict | None = None) -> object:
    with httpx.Client(timeout=20) as client:
        response = client.request(method, f"{OWUI_URL}{path}", params=params,
                                  json=body,
                                  headers={"Authorization": f"Bearer {token}"})
    if response.status_code not in (200, 201):
        raise HTTPException(response.status_code, f"Open WebUI: {path}")
    if not response.content:
        return {}
    try:
        return response.json()
    except Exception:
        return {}


def _vikunja_json(method: str, path: str, body: dict | None = None,
                  params: dict | None = None) -> object:
    return _vikunja(method, path, body=body, params=params)


DATA_DIR = Path(os.environ.get("PORTAL_DATA", "/data"))
PORTAL_DB = DATA_DIR / "portal.db"


def _portal_db() -> sqlite3.Connection:
    conn = sqlite3.connect(PORTAL_DB, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _user_vault(user: dict) -> tuple[dict | None, str, str]:
    """Возвращает (config, token_plain, uid)."""
    uid = str(user.get("id") or user.get("email") or "")
    if not uid:
        return None, "", ""
    try:
        conn = _portal_db()
        user_vaults.ensure_table(conn)
        cfg = user_vaults.get_config(conn, uid)
        conn.close()
    except Exception:
        return None, "", uid
    token = user_vaults.decrypt_token(DATA_DIR, (cfg or {}).get("encrypted_token", ""))
    return cfg, token, uid


def _place() -> tuple[str, float, float]:
    """Город владельца из настроек бота, иначе значение по умолчанию."""
    try:
        conn = sqlite3.connect(BOT_DB_PATH, timeout=5)
        row = conn.execute(
            "SELECT city, lat, lon FROM settings ORDER BY tg_id LIMIT 1").fetchone()
        conn.close()
        if row:
            return str(row[0]), float(row[1]), float(row[2])
    except sqlite3.Error:
        pass
    return DEFAULT_CITY, DEFAULT_COORDS[0], DEFAULT_COORDS[1]


def _weather(lat: float, lon: float) -> dict:
    query = urllib.parse.urlencode({"lat": f"{lat:.4f}", "lon": f"{lon:.4f}"})
    try:
        with httpx.Client(timeout=15) as client:
            response = client.get(
                f"https://api.met.no/weatherapi/locationforecast/2.0/compact?{query}",
                headers={"User-Agent": WEATHER_UA})
        data = response.json()
        point = data["properties"]["timeseries"][0]
        details = point["data"]["instant"]["details"]
        symbol = (point["data"].get("next_1_hours", {}).get("summary", {})
                  .get("symbol_code", ""))
        return {"temperature": round(float(details["air_temperature"])),
                "wind": round(float(details.get("wind_speed", 0))),
                "symbol": symbol}
    except Exception:  # noqa: BLE001 — погода не должна ломать «Сегодня»
        return {}


def _events(token: str) -> list[dict]:
    now = datetime.now()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    try:
        items = _owui(token, "/api/v1/calendars/events", {
            "start": start.strftime("%Y-%m-%dT%H:%M:%S"),
            "end": end.strftime("%Y-%m-%dT%H:%M:%S"),
        })
    except HTTPException:
        return []
    events = items if isinstance(items, list) else items.get("events", [])
    result = []
    for event in events:
        start_at = event.get("start_at") or event.get("startAt") or 0
        moment = datetime.fromtimestamp(int(start_at) / 1e9) if start_at else None
        result.append({
            "id": event.get("id", ""),
            "title": event.get("title", ""),
            "time": moment.strftime("%H:%M") if moment else "",
            "all_day": bool(event.get("all_day") or event.get("allDay")),
        })
    return sorted(result, key=lambda item: item["time"])


def _tasks() -> list[dict]:
    if not VIKUNJA_TOKEN:
        return []
    try:
        with httpx.Client(timeout=15) as client:
            response = client.get(
                f"{VIKUNJA_URL}/api/v1/tasks",
                params={"filter": "done = false", "per_page": 20},
                headers={"Authorization": f"Bearer {VIKUNJA_TOKEN}"})
        tasks = response.json() if response.status_code == 200 else []
    except Exception:  # noqa: BLE001
        return []
    result = []
    for task in tasks[:20]:
        result.append({
            "id": task.get("id"),
            "title": task.get("title", ""),
            "due": (task.get("due_date") or "")[:10],
            "priority": task.get("priority", 0),
        })
    return result


def _notes(token: str) -> list[dict]:
    try:
        notes = _owui(token, "/api/v1/notes/")
    except HTTPException:
        return []
    items = notes if isinstance(notes, list) else notes.get("data", [])
    return [{"id": note.get("id", ""), "title": note.get("title", "")}
            for note in items[:5]]


# Персональные вольты: авторизация через сессию пользователя, не через Basic Auth владельца.


def _owui_call(token: str, method: str, path: str,
               body: dict | None = None,
               params: dict | None = None) -> object:
    with httpx.Client(timeout=20) as client:
        response = client.request(method, f"{OWUI_URL}{path}", params=params,
                                  json=body,
                                  headers={"Authorization": f"Bearer {token}"})
    if response.status_code not in (200, 201):
        raise HTTPException(response.status_code, f"Open WebUI: {path}")
    if not response.content:
        return {}
    return response.json()


def _ensure_calendar(token: str) -> str:
    """Возвращает id первого календаря пользователя, создаёт при отсутствии."""
    calendars = _owui_call(token, "GET", "/api/v1/calendars/")
    if calendars:
        return str(calendars[0]["id"])
    created = _owui_call(token, "POST", "/api/v1/calendars/create",
                         body={"name": "Личный", "color": "#d79921"})
    return str(created["id"])


# ── Эндпоинты ────────────────────────────────────────────────────────────

_today_cache: dict[str, tuple[float, dict]] = {}


def _today_data(token: str) -> dict:
    """Сводка «Сегодня»: источники опрашиваем параллельно и кэшируем на 20 с.

    Погода через api.met.no often отвечает секундами; без параллельности
    приложение ждёт все источники последовательно и кажется «зависшим».
    """
    now = time.time()
    cached = _today_cache.get(token)
    if cached and cached[0] > now:
        return cached[1]
    city, lat, lon = _place()
    tasks = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {
            "weather": pool.submit(_weather, lat, lon),
            "events": pool.submit(_events, token),
            "tasks": pool.submit(_tasks),
            "notes": pool.submit(_notes, token),
        }
        for key, future in futures.items():
            try:
                tasks[key] = future.result(timeout=12)
            except Exception:  # noqa: BLE001 — один источник не ломает сводку
                tasks[key] = [] if key != "weather" else {}
    data = {
        "place": city,
        "weather": tasks.get("weather") or {},
        "events": tasks.get("events") or [],
        "tasks": tasks.get("tasks") or [],
        "notes": tasks.get("notes") or [],
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    _today_cache[token] = (now + 20, data)
    return data


@router.get("/today")
def today(request: Request) -> JSONResponse:
    token = _token(request)
    _user(token)
    return JSONResponse(_today_data(token))


@router.get("/voices")
def voices(request: Request, engine: str = "local") -> JSONResponse:
    """Список голосов: локальный TTS или облачный, если он настроен."""
    token = _token(request)
    _user(token)
    if engine == "cloud":
        if not CLOUD_TTS_URL or not CLOUD_TTS_KEY:
            return JSONResponse({"detail": "облачный TTS не настроен"}, status_code=503)
        try:
            with httpx.Client(timeout=30) as cloud_client:
                cloud_resp = cloud_client.get(
                    CLOUD_TTS_URL.rstrip("/") + "/audio/voices",
                    headers={"Authorization": f"Bearer {CLOUD_TTS_KEY}"},
                )
        except httpx.HTTPError:
            return JSONResponse({"detail": "облачный TTS недоступен"}, status_code=503)
        if cloud_resp.status_code >= 400:
            return JSONResponse({"detail": "ошибка облачного TTS"}, status_code=502)
        return JSONResponse(cloud_resp.json())
    try:
        with httpx.Client(timeout=30) as client:
            response = client.get(f"{TTS_URL}/v1/audio/voices")
    except httpx.HTTPError:
        raise HTTPException(503, "список голосов недоступен")
    if response.status_code != 200:
        raise HTTPException(502, "ошибка получения списка голосов")
    return JSONResponse(response.json())


@router.post("/voice")
async def voice(
    request: Request,
    file: UploadFile = File(...),
    engine: str = Form("local"),
    model: str = Form(""),
    language: str = Form("ru"),
) -> JSONResponse:
    token = _token(request)
    _user(token)
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "пустой файл")
    if len(raw) > 10 * 1024 * 1024:
        raise HTTPException(413, "аудио больше 10 МБ")
    try:
        if engine == "cloud":
            if not CLOUD_STT_URL or not CLOUD_STT_KEY:
                return JSONResponse({"detail": "облачный STT не настроен"}, status_code=503)
            try:
                with httpx.Client(timeout=120) as cloud_client:
                    cloud_resp = cloud_client.post(
                        CLOUD_STT_URL.rstrip("/") + "/audio/transcriptions",
                        headers={"Authorization": f"Bearer {CLOUD_STT_KEY}"},
                        files={"file": (file.filename or "audio.m4a", raw,
                                        file.content_type or "audio/mp4")},
                        data={"model": model or CLOUD_STT_MODEL, "language": language or "ru"},
                    )
                if cloud_resp.status_code >= 400:
                    return JSONResponse({"detail": "ошибка облачного распознавания"}, status_code=502)
                return JSONResponse({"text": str((cloud_resp.json() or {}).get("text") or "")})
            except Exception:
                return JSONResponse({"detail": "облачный STT недоступен"}, status_code=503)
        with httpx.Client(timeout=120) as client:
            response = client.post(
                f"{STT_URL}/v1/audio/transcriptions",
                files={"file": (file.filename or "audio.m4a", raw,
                                file.content_type or "audio/mp4")},
                data={"model": model or "vosk", "language": language or "ru"})
    except httpx.HTTPError:
        raise HTTPException(503, "распознавание недоступно")
    if response.status_code != 200:
        raise HTTPException(502, "ошибка распознавания")
    return JSONResponse({"text": response.json().get("text", "")})


# Локальный контейнер TTS отвечает 413 на вход длиннее TTS_MAX_CHARS (1000).
TTS_CHUNK_CHARS = 1000
_SENTENCE_END = ".!?…"


def _split_tts_text(text: str, limit: int = TTS_CHUNK_CHARS) -> list[str]:
    """Разбивает текст на куски ≤ limit по границам предложений, затем слов."""
    sentences: list[str] = []
    start = 0
    for i, char in enumerate(text):
        if char != "\n" and char not in _SENTENCE_END:
            continue
        end = i + 1
        if char != "\n":
            while end < len(text) and text[end] in "\"'”’»)]}":
                end += 1
            # точка в числах и сокращениях — не конец предложения
            if end < len(text) and not text[end].isspace():
                continue
        sentences.append(text[start:end])
        start = end
    if start < len(text):
        sentences.append(text[start:])

    chunks: list[str] = []
    current = ""

    def flush() -> None:
        nonlocal current
        if current:
            chunks.append(current)
            current = ""

    def append(part: str) -> None:
        nonlocal current
        if not part:
            return
        if current and len(current) + 1 + len(part) > limit:
            flush()
        current = f"{current} {part}".strip()

    for sentence in sentences:
        words = sentence.split()
        if not words:
            continue
        if len(" ".join(words)) <= limit:
            append(" ".join(words))
            continue
        for word in words:
            if len(word) > limit:
                flush()
                for pos in range(0, len(word), limit):
                    piece = word[pos:pos + limit]
                    if pos + limit < len(word):
                        chunks.append(piece)
                    else:
                        append(piece)
            else:
                append(word)
    flush()
    return chunks


def _concat_wav(parts: list[bytes]) -> bytes:
    """Склеивает WAV-части в один файл с единственным заголовком."""
    if not parts:
        raise HTTPException(502, "TTS не вернул аудио")
    buffer = io.BytesIO()
    try:
        with wave.open(buffer, "wb") as out:
            for index, part in enumerate(parts):
                with wave.open(io.BytesIO(part), "rb") as src:
                    params = src.getparams()
                    if src.getcomptype() != "NONE":
                        raise HTTPException(502, "TTS вернул сжатый WAV")
                    if index == 0:
                        out.setparams(params)
                    elif (params.nchannels, params.sampwidth, params.framerate) != (
                            out.getnchannels(), out.getsampwidth(), out.getframerate()):
                        raise HTTPException(502, "несовместимые WAV-части от TTS")
                    out.writeframes(src.readframes(src.getnframes()))
    except wave.Error:
        raise HTTPException(502, "TTS вернул некорректный WAV")
    return buffer.getvalue()


@router.post("/speak")
async def speak(request: Request) -> Response:
    token = _token(request)
    _user(token)
    data = await request.json()
    text = str(data.get("text") or "").strip()
    if not text:
        raise HTTPException(400, "пустой текст")
    if len(text) > 4000:
        text = text[:4000]
    engine = str(data.get("engine") or "local")
    model = str(data.get("model") or "tts")
    voice = str(data.get("voice") or "baya")
    response_format = str(data.get("response_format") or "wav")
    if engine == "cloud":
        if not CLOUD_TTS_URL or not CLOUD_TTS_KEY:
            return JSONResponse({"detail": "облачный TTS не настроен"}, status_code=503)
        payload = {"model": model or CLOUD_TTS_MODEL, "input": text,
                   "voice": voice, "response_format": response_format}
        try:
            with httpx.Client(timeout=120) as cloud_client:
                cloud_resp = cloud_client.post(
                    CLOUD_TTS_URL.rstrip("/") + "/audio/speech",
                    headers={"Authorization": f"Bearer {CLOUD_TTS_KEY}"},
                    json=payload,
                )
        except httpx.HTTPError:
            raise HTTPException(503, "облачный синтез речи недоступен")
        if cloud_resp.status_code >= 400:
            raise HTTPException(502, "ошибка облачного синтеза речи")
        media_type = {"mp3": "audio/mpeg"}.get(response_format, f"audio/{response_format}")
        return Response(content=cloud_resp.content, media_type=media_type)
    # Локальный TTS всегда отдаёт WAV, какой бы формат ни запросил клиент.
    # Длинный текст режем по предложениям/словам: контейнер не примет >1000 символов.
    payload = {"model": model, "voice": voice,
               "response_format": response_format}
    wav_parts: list[bytes] = []
    try:
        with httpx.Client(timeout=120) as client:
            for chunk in _split_tts_text(text):
                response = client.post(f"{TTS_URL}/v1/audio/speech",
                                       json={**payload, "input": chunk})
                if response.status_code != 200:
                    raise HTTPException(502, "ошибка синтеза речи")
                wav_parts.append(response.content)
    except httpx.HTTPError:
        raise HTTPException(503, "синтез речи недоступен")
    return Response(content=_concat_wav(wav_parts), media_type="audio/wav")


@router.post("/note")
async def note(request: Request) -> JSONResponse:
    """Создать заметку в личном Obsidian-вольте пользователя (AI/*, по правам)."""
    token = _token(request)
    user = _user(token)
    data = await request.json()
    text = str(data.get("text") or "").strip()
    if not text:
        raise HTTPException(400, "пустой текст")
    title = str(data.get("title") or "").strip()
    folder = str(data.get("folder") or "AI/Inbox").strip() or "AI/Inbox"
    cfg, vtoken, uid = _user_vault(user)
    if not cfg or not cfg.get("repo_url"):
        raise HTTPException(409, "личный вольт не настроен")
    if not user_vaults.is_ready(uid):
        raise HTTPException(409, "вольт не подключён (выполните синхронизацию)")
    try:
        conn = _portal_db()
        user_vaults.ensure_table(conn)
        result = user_vaults.create_note(uid, conn, vtoken, folder, title, text)
        conn.close()
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        msg = str(e)
        # не светим токен/секреты
        if "x-access-token" in msg or "AUTHORIZATION" in msg.upper():
            msg = "ошибка git-операции"
        raise HTTPException(502, msg[:300] or "не удалось записать заметку")
    return JSONResponse(result)


@router.post("/reminder")
async def reminder(request: Request) -> JSONResponse:
    token = _token(request)
    _user(token)
    data = await request.json()
    text = str(data.get("text") or "").strip()
    due = str(data.get("due_at") or "").strip()
    if not text or not due:
        raise HTTPException(400, "нужны text и due_at")
    try:
        moment = datetime.fromisoformat(due.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(400, "due_at в формате ISO 8601")
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    moment = moment.astimezone(timezone.utc)

    telegram = bool(data.get("telegram"))
    if telegram and TELEGRAM_OWNER_ID:
        try:
            conn = sqlite3.connect(BOT_DB_PATH, timeout=5)
            conn.execute(
                "INSERT INTO reminders (tg_id, chat_id, text, due_at) "
                "VALUES (?, ?, ?, ?)",
                (int(TELEGRAM_OWNER_ID), int(TELEGRAM_OWNER_ID), text,
                 moment.isoformat(timespec="seconds")))
            conn.commit()
            conn.close()
        except (sqlite3.Error, ValueError):
            raise HTTPException(500, "не удалось сохранить напоминание")
    return JSONResponse({
        "ok": True,
        "due_at": moment.isoformat(timespec="seconds"),
        "telegram": bool(telegram and TELEGRAM_OWNER_ID),
    })


@router.post("/event")
async def create_event(request: Request) -> JSONResponse:
    """Создать событие в календаре Open WebUI.

    start — ISO 8601 (YYYY-MM-DDTHH:MM или с зоной). duration_min — длина,
    по умолчанию 60. Время хранится в наносекундах (особенность Open WebUI).
    """
    token = _token(request)
    _user(token)
    data = await request.json()
    title = str(data.get("title") or "").strip()
    start_raw = str(data.get("start") or "").strip()
    if not title or not start_raw:
        raise HTTPException(400, "нужны title и start")
    try:
        start = datetime.fromisoformat(start_raw.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(400, "start в формате ISO 8601")
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone(timedelta(hours=3)))  # Europe/Moscow
    duration = int(data.get("duration_min") or 60)
    end = start + timedelta(minutes=max(5, duration))
    calendar_id = _ensure_calendar(token)
    payload = {
        "calendar_id": calendar_id,
        "title": title,
        "description": str(data.get("description") or "").strip() or None,
        "start_at": int(start.timestamp() * 1_000_000_000),
        "end_at": int(end.timestamp() * 1_000_000_000),
        "is_all_day": False,
        "location": str(data.get("location") or "").strip() or None,
    }
    event = _owui_call(token, "POST", "/api/v1/calendars/events/create", body=payload)
    return JSONResponse({"ok": True, "id": event.get("id"), "title": title})


@router.post("/event/{event_id}/delete")
async def delete_event(event_id: str, request: Request) -> JSONResponse:
    token = _token(request)
    _user(token)
    _owui_call(token, "DELETE", f"/api/v1/calendars/events/{event_id}/delete")
    return JSONResponse({"ok": True})


@router.post("/task")
async def create_task(request: Request) -> JSONResponse:
    """Создать задачу в Vikunja (общий список, токен на сервере)."""
    token = _token(request)
    _user(token)
    if not VIKUNJA_TOKEN:
        raise HTTPException(503, "Vikunja не настроен")
    data = await request.json()
    title = str(data.get("title") or "").strip()
    if not title:
        raise HTTPException(400, "нужен title")
    payload: dict[str, object] = {"title": title}
    due = str(data.get("due") or "").strip()
    if due:
        try:
            day = datetime.fromisoformat(due.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(400, "due в формате YYYY-MM-DD")
        payload["due_date"] = day.strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        with httpx.Client(timeout=20) as client:
            response = client.put(
                f"{VIKUNJA_URL}/api/v1/projects/1/tasks",
                json=payload,
                headers={"Authorization": f"Bearer {VIKUNJA_TOKEN}"})
    except httpx.HTTPError:
        raise HTTPException(503, "Vikunja недоступен")
    if response.status_code not in (200, 201):
        detail = ""
        try:
            detail = str(response.json().get("message", ""))
        except Exception:  # noqa: BLE001
            detail = response.text[:120]
        raise HTTPException(response.status_code, detail or "не удалось создать задачу")
    task = response.json()
    return JSONResponse({"ok": True, "id": task.get("id"), "title": title})


@router.get("/status")
def status(request: Request) -> JSONResponse:
    token = _token(request)
    user = _user(token)
    if not _is_owner(user):
        raise HTTPException(403, "только владелец")
    try:
        return JSONResponse(json.loads(STATUS_FILE.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        raise HTTPException(503, "метрики ещё не собраны")


# ── Задачи (Vikunja) ────────────────────────────────────────────────────

def _vikunja(method: str, path: str, body: dict | None = None,
             params: dict | None = None) -> object:
    if not VIKUNJA_TOKEN:
        raise HTTPException(503, "Vikunja не настроен")
    try:
        with httpx.Client(timeout=20) as client:
            response = client.request(
                method, f"{VIKUNJA_URL}{path}", json=body, params=params,
                headers={"Authorization": f"Bearer {VIKUNJA_TOKEN}"})
    except httpx.HTTPError:
        raise HTTPException(503, "Vikunja недоступен")
    if response.status_code == 204:
        return {}
    if response.status_code not in (200, 201):
        detail = ""
        try:
            detail = str(response.json().get("message", ""))
        except Exception:  # noqa: BLE001
            detail = response.text[:120]
        raise HTTPException(response.status_code, detail or "ошибка Vikunja")
    return response.json() if response.content else {}


@router.get("/tasks")
def list_tasks(request: Request) -> JSONResponse:
    token = _token(request)
    _user(token)
    tasks = _vikunja("GET", "/api/v1/tasks",
                     params={"filter": "done = false", "per_page": 50})
    items = []
    for task in (tasks if isinstance(tasks, list) else []):
        items.append({
            "id": task.get("id"),
            "title": task.get("title", ""),
            "description": task.get("description", ""),
            "due": (task.get("due_date") or "")[:10],
            "priority": task.get("priority", 0),
            "done": bool(task.get("done")),
        })
    return JSONResponse({"tasks": items})


@router.post("/task/{task_id}/done")
async def complete_task(task_id: int, request: Request) -> JSONResponse:
    token = _token(request)
    _user(token)
    data = await request.json()
    done = bool(data.get("done", True))
    _vikunja("POST", f"/api/v1/tasks/{task_id}", body={"done": done})
    return JSONResponse({"ok": True})


@router.post("/task/{task_id}/delete")
async def delete_task(task_id: int, request: Request) -> JSONResponse:
    token = _token(request)
    _user(token)
    _vikunja("DELETE", f"/api/v1/tasks/{task_id}")
    return JSONResponse({"ok": True})


# ── Календарь (расширенный) ─────────────────────────────────────────────

@router.get("/events")
def list_events(request: Request, days: int = 7) -> JSONResponse:
    token = _token(request)
    _user(token)
    days = max(1, min(days, 30))
    now = datetime.now()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=days)
    try:
        items = _owui(token, "/api/v1/calendars/events", {
            "start": start.strftime("%Y-%m-%dT%H:%M:%S"),
            "end": end.strftime("%Y-%m-%dT%H:%M:%S"),
        })
    except HTTPException:
        items = []
    events = items if isinstance(items, list) else items.get("events", [])
    result = []
    for event in events:
        start_at = event.get("start_at") or event.get("startAt") or 0
        moment = datetime.fromtimestamp(int(start_at) / 1e9) if start_at else None
        result.append({
            "id": event.get("id", ""),
            "title": event.get("title", ""),
            "description": event.get("description") or "",
            "time": moment.strftime("%Y-%m-%d %H:%M") if moment else "",
            "all_day": bool(event.get("all_day") or event.get("allDay")),
        })
    result.sort(key=lambda item: item["time"])
    return JSONResponse({"events": result})


# ── Заметки Open WebUI ──────────────────────────────────────────────────

@router.get("/notes")
def list_notes(request: Request) -> JSONResponse:
    token = _token(request)
    _user(token)
    notes = _owui(token, "/api/v1/notes/")
    items = notes if isinstance(notes, list) else notes.get("data", [])
    result = []
    for note in items:
        result.append({
            "id": note.get("id", ""),
            "title": note.get("title", ""),
            "updated_at": note.get("updated_at", note.get("updatedAt", 0)),
        })
    result.sort(key=lambda n: n.get("updated_at") or 0, reverse=True)
    return JSONResponse({"notes": result})


@router.get("/notes/{note_id}")
def get_note(note_id: str, request: Request) -> JSONResponse:
    token = _token(request)
    _user(token)
    note = _owui(token, f"/api/v1/notes/{note_id}")
    content = note.get("data", {}).get("content", {})
    if isinstance(content, dict):
        text = content.get("md", content.get("text", json.dumps(content, ensure_ascii=False)))
    else:
        text = str(content)
    return JSONResponse({
        "id": note.get("id", note_id),
        "title": note.get("title", ""),
        "content": text,
        "updated_at": note.get("updated_at", note.get("updatedAt", 0)),
    })


@router.post("/notes")
async def create_owui_note(request: Request) -> JSONResponse:
    token = _token(request)
    _user(token)
    data = await request.json()
    title = str(data.get("title") or "Без названия").strip()
    content = str(data.get("content") or "").strip()
    body = {"title": title, "data": {"content": {"md": content, "html": "", "json": None}}}
    note = _owui_call(token, "POST", "/api/v1/notes/create", body=body)
    return JSONResponse({"ok": True, "id": note.get("id"), "title": note.get("title", title)})


@router.post("/notes/{note_id}")
async def update_owui_note(note_id: str, request: Request) -> JSONResponse:
    token = _token(request)
    _user(token)
    data = await request.json()
    body: dict[str, object] = {}
    if "title" in data:
        body["title"] = str(data["title"]).strip()
    if "content" in data:
        body["data"] = {"content": {"md": str(data["content"]), "html": "", "json": None}}
    note = _owui_call(token, "POST", f"/api/v1/notes/{note_id}/update", body=body)
    return JSONResponse({"ok": True, "id": note.get("id", note_id)})


@router.post("/notes/{note_id}/delete")
async def delete_owui_note(note_id: str, request: Request) -> JSONResponse:
    token = _token(request)
    _user(token)
    _owui_call(token, "DELETE", f"/api/v1/notes/{note_id}/delete")
    return JSONResponse({"ok": True})



# ── Личный Obsidian-вольт пользователя ────────────────────────────────────
# Репозиторий и токен GitHub настраиваются пользователем; права на папки
# (читать/писать/изменять/создавать и т.д.) задаёт сам пользователь и
# проверяются программно на каждом запросе — промпт обход не даёт.

def _vault_public_config(cfg: dict | None, uid: str) -> dict:
    if not cfg or not cfg.get("repo_url"):
        return {"configured": False, "ready": False}
    ready = user_vaults.is_ready(uid) if uid else False
    return {
        "configured": True,
        "ready": ready,
        "repo_url": cfg.get("repo_url", ""),
        "branch": cfg.get("branch", "main"),
        "write_prefix": cfg.get("write_prefix", "AI"),
        "status": cfg.get("status", ""),
        "last_sync": cfg.get("last_sync", ""),
        "last_error": cfg.get("last_error", ""),
        "updated_at": cfg.get("updated_at", ""),
    }


@router.get("/vault/config")
def vault_config(request: Request) -> JSONResponse:
    token = _token(request)
    user = _user(token)
    cfg, _vtoken, uid = _user_vault(user)
    data = _vault_public_config(cfg, uid)
    try:
        conn = _portal_db()
        user_vaults.ensure_table(conn)
        data["permissions"] = user_vaults.get_permissions(conn, uid)
        data["has_token"] = bool((cfg or {}).get("encrypted_token"))
        conn.close()
    except Exception:
        data["permissions"] = []
        data["has_token"] = False
    return JSONResponse(data)


@router.post("/vault/config")
async def vault_config_set(request: Request) -> JSONResponse:
    token = _token(request)
    user = _user(token)
    body = await request.json()
    repo_url = str(body.get("repo_url") or "").strip()
    branch = str(body.get("branch") or "main").strip() or "main"
    pat = str(body.get("token") or body.get("pat") or "").strip()
    permissions = body.get("permissions")
    uid = str(user.get("id") or user.get("email") or "")
    if not uid:
        raise HTTPException(400, "не удалось определить пользователя")
    if not repo_url:
        raise HTTPException(400, "нужен repo_url")
    try:
        repo_url = user_vaults._clean_repo_url(repo_url)
    except ValueError as e:
        raise HTTPException(400, str(e))
    conn = _portal_db()
    user_vaults.ensure_table(conn)
    enc = user_vaults.encrypt_token(DATA_DIR, pat) if pat else ""
    # если токен не передан, оставляем прежний
    old = user_vaults.get_config(conn, uid)
    if not enc and old:
        enc = old.get("encrypted_token", "")
    if not enc and not old:
        conn.close()
        raise HTTPException(400, "нужен токен GitHub (PAT) для доступа к репозиторию")
    try:
        if permissions is not None:
            perms = user_vaults.normalize_permissions(permissions)
        else:
            perms = user_vaults.get_permissions(conn, uid) if old else None
    except ValueError as e:
        conn.close()
        raise HTTPException(400, str(e))
    user_vaults.upsert_config(conn, uid, repo_url, branch, enc,
                              write_prefix="AI", permissions=perms)
    user_vaults.set_state(conn, uid, status="configured", error="")
    conn.close()
    # попытка клонирования/обновления
    plain_token = pat or user_vaults.decrypt_token(DATA_DIR, enc)
    try:
        user_vaults.clone_or_update(uid, repo_url, branch, plain_token)
        conn = _portal_db()
        user_vaults.set_state(conn, uid, status="ready", error="", synced=True)
        conn.close()
        return JSONResponse({"ok": True, "status": "ready"})
    except Exception as e:
        msg = str(e)
        if "x-access-token" in msg or "AUTHORIZATION" in msg.upper():
            msg = "ошибка доступа к репозиторию (проверьте токен и права)"
        conn = _portal_db()
        user_vaults.set_state(conn, uid, status="error", error=msg[:400])
        conn.close()
        return JSONResponse({"ok": False, "status": "error", "error": msg[:300]}, status_code=400)


@router.post("/vault/sync")
async def vault_sync(request: Request) -> JSONResponse:
    token = _token(request)
    user = _user(token)
    cfg, vtoken, uid = _user_vault(user)
    if not cfg or not cfg.get("repo_url"):
        raise HTTPException(409, "личный вольт не настроен")
    try:
        if not user_vaults.is_ready(uid):
            user_vaults.clone_or_update(uid, cfg.get("repo_url", ""), cfg.get("branch", "main"), vtoken)
        result = user_vaults.sync(uid, vtoken)
        conn = _portal_db()
        user_vaults.set_state(conn, uid, status="ready", error="", synced=True)
        conn.close()
        return JSONResponse({"ok": True, **result})
    except Exception as e:
        msg = str(e)
        if "x-access-token" in msg or "AUTHORIZATION" in msg.upper():
            msg = "ошибка доступа к репозиторию"
        conn = _portal_db()
        user_vaults.set_state(conn, uid, status="error", error=msg[:400])
        conn.close()
        raise HTTPException(502, msg[:300] or "ошибка синхронизации")


@router.post("/vault/disconnect")
async def vault_disconnect(request: Request) -> JSONResponse:
    token = _token(request)
    user = _user(token)
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    remove_local = bool(body.get("remove_local", False))
    _cfg, _vtoken, uid = _user_vault(user)
    if not uid:
        raise HTTPException(400, "не удалось определить пользователя")
    conn = _portal_db()
    user_vaults.delete_config(conn, uid)
    conn.close()
    if remove_local:
        try:
            user_vaults.remove_local(uid)
        except Exception:
            pass
    return JSONResponse({"ok": True})


@router.get("/vault/permissions")
def vault_permissions_get(request: Request) -> JSONResponse:
    token = _token(request)
    user = _user(token)
    cfg, _vtoken, uid = _user_vault(user)
    if not cfg:
        return JSONResponse({"permissions": user_vaults.DEFAULT_PERMISSIONS, "configured": False})
    conn = _portal_db()
    perms = user_vaults.get_permissions(conn, uid)
    conn.close()
    return JSONResponse({"permissions": perms, "configured": True})


@router.post("/vault/permissions")
async def vault_permissions_set(request: Request) -> JSONResponse:
    token = _token(request)
    user = _user(token)
    body = await request.json()
    perms_in = body.get("permissions")
    try:
        perms = user_vaults.normalize_permissions(perms_in)
    except ValueError as e:
        raise HTTPException(400, str(e))
    cfg, _vtoken, uid = _user_vault(user)
    conn = _portal_db()
    user_vaults.ensure_table(conn)
    if not cfg:
        conn.close()
        raise HTTPException(409, "сначала настройте вольт")
    user_vaults.set_permissions(conn, uid, perms)
    conn.close()
    return JSONResponse({"ok": True, "permissions": perms})


@router.get("/vault/files")
def vault_files(request: Request, q: str = "", limit: int = 50) -> JSONResponse:
    token = _token(request)
    user = _user(token)
    cfg, _vtoken, uid = _user_vault(user)
    if not cfg or not cfg.get("repo_url"):
        return JSONResponse({"files": [], "configured": False})
    if not user_vaults.is_ready(uid):
        return JSONResponse({"files": [], "configured": True, "ready": False, "note": "вольт не подключён"})
    try:
        conn = _portal_db()
        files = user_vaults.list_files(uid, conn, q=q, limit=limit)
        conn.close()
    except Exception as e:
        raise HTTPException(500, str(e)[:200])
    return JSONResponse({"files": files, "configured": True, "ready": True})


@router.get("/vault/read")
def vault_read(request: Request, path: str) -> JSONResponse:
    token = _token(request)
    user = _user(token)
    cfg, _vtoken, uid = _user_vault(user)
    if not cfg or not user_vaults.is_ready(uid):
        raise HTTPException(409, "вольт не подключён")
    try:
        conn = _portal_db()
        result = user_vaults.read_note(uid, conn, path)
        conn.close()
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except FileNotFoundError:
        raise HTTPException(404, "файл не найден")
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, str(e)[:200])
    content = result.get("content", "")
    if len(content) > 30000:
        content = content[:30000] + "\n…(обрезано)"
        result["content"] = content
        result["truncated"] = True
    return JSONResponse(result)


@router.get("/vault/search")
def vault_search(request: Request, q: str, limit: int = 20) -> JSONResponse:
    token = _token(request)
    user = _user(token)
    query = (q or "").strip()
    if not query:
        raise HTTPException(400, "нужен q")
    cfg, _vtoken, uid = _user_vault(user)
    if not cfg or not user_vaults.is_ready(uid):
        return JSONResponse({"results": [], "configured": bool(cfg and cfg.get("repo_url")), "ready": False})
    try:
        conn = _portal_db()
        results = user_vaults.search(uid, conn, query, limit=limit)
        conn.close()
    except Exception as e:
        raise HTTPException(500, str(e)[:200])
    return JSONResponse({"results": results, "configured": True, "ready": True})


@router.post("/vault/note")
async def vault_note(request: Request) -> JSONResponse:
    """Создать заметку в личном вольте с явным выбором папки (в пределах прав)."""
    token = _token(request)
    user = _user(token)
    data = await request.json()
    text = str(data.get("text") or "").strip()
    if not text:
        raise HTTPException(400, "пустой текст")
    title = str(data.get("title") or "").strip()
    folder = str(data.get("folder") or "AI/Inbox").strip() or "AI/Inbox"
    cfg, vtoken, uid = _user_vault(user)
    if not cfg or not user_vaults.is_ready(uid):
        raise HTTPException(409, "личный вольт не подключён")
    try:
        conn = _portal_db()
        user_vaults.ensure_table(conn)
        result = user_vaults.create_note(uid, conn, vtoken, folder, title, text)
        conn.close()
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        msg = str(e)
        if "x-access-token" in msg or "AUTHORIZATION" in msg.upper():
            msg = "ошибка git-операции"
        raise HTTPException(502, msg[:300] or "не удалось записать заметку")
    return JSONResponse(result)



# ── Напоминания (bot.db) ────────────────────────────────────────────────

BOT_DB_PATH = Path(os.environ.get("BOT_DB_PATH", "/bot/bot.db"))


@router.get("/reminders")
def list_reminders(request: Request) -> JSONResponse:
    token = _token(request)
    user = _user(token)
    if not BOT_DB_PATH.is_file():
        return JSONResponse({"reminders": []})
    try:
        conn = sqlite3.connect(str(BOT_DB_PATH), timeout=5)
        conn.row_factory = sqlite3.Row
        # Владелец видит все несработавшие; остальные — по привязке tg_id если есть.
        tg_id = 0
        try:
            row = conn.execute(
                "SELECT tg_id FROM accounts WHERE owui_login = ? ORDER BY id DESC LIMIT 1",
                (user.get("email", ""),)).fetchone()
            if row:
                tg_id = int(row["tg_id"] or 0)
        except sqlite3.Error:
            pass
        if _is_owner(user) and tg_id == 0:
            rows = conn.execute(
                "SELECT id, tg_id, text, due_at, sent FROM reminders WHERE sent = 0 "
                "ORDER BY due_at LIMIT 50").fetchall()
        elif tg_id:
            rows = conn.execute(
                "SELECT id, tg_id, text, due_at, sent FROM reminders WHERE tg_id = ? AND sent = 0 "
                "ORDER BY due_at LIMIT 50", (tg_id,)).fetchall()
        else:
            rows = []
        conn.close()
    except sqlite3.Error:
        return JSONResponse({"reminders": []})
    items = [{"id": r["id"], "text": r["text"], "due_at": r["due_at"]}
             for r in rows]
    return JSONResponse({"reminders": items})


@router.post("/reminders/{reminder_id}/delete")
async def delete_reminder(reminder_id: int, request: Request) -> JSONResponse:
    token = _token(request)
    user = _user(token)
    if not BOT_DB_PATH.is_file():
        raise HTTPException(404, "нет базы напоминаний")
    try:
        conn = sqlite3.connect(str(BOT_DB_PATH), timeout=5)
        if _is_owner(user):
            cur = conn.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))
        else:
            cur = conn.execute(
                "DELETE FROM reminders WHERE id = ? AND tg_id IN "
                "(SELECT tg_id FROM accounts WHERE owui_login = ?)",
                (reminder_id, user.get("email", "")))
        conn.commit()
        deleted = cur.rowcount
        conn.close()
    except sqlite3.Error:
        raise HTTPException(500, "ошибка базы")
    if not deleted:
        raise HTTPException(404, "напоминание не найдено")
    return JSONResponse({"ok": True})

@router.post("/event/{event_id}/update")
async def mobile_update_event(event_id: str, request: Request):
    token = _token(request)
    _user(token)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "ожидается JSON")
    payload = {}
    title = str(body.get("title") or "").strip()
    if title:
        payload["title"] = title
    for field in ("start", "start_at", "end", "end_at", "description", "location"):
        if field in body and body.get(field) is not None:
            payload[field] = body.get(field)
    if not payload:
        raise HTTPException(400, "нет полей для обновления")
    # Open WebUI: разные сборки принимают update по разным путям.
    for path in (
        f"/api/v1/calendars/events/{event_id}/update",
        "/api/v1/calendars/events/update",
    ):
        data = dict(payload)
        if path.endswith("/update") and "events/" not in path.replace(f"/{event_id}/", "/"):
            data["id"] = event_id
        try:
            res = _owui_json("POST", path, token, data)
            return JSONResponse(res if isinstance(res, dict) else {"ok": True, "event": res})
        except HTTPException as exc:
            if exc.status_code in (404, 405):
                continue
            raise
    raise HTTPException(400, "обновление события не поддерживается этой версией Open WebUI")


@router.post("/task/{task_id}/update")
async def mobile_update_task(task_id: str, request: Request):
    token = _token(request)
    _user(token)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "ожидается JSON")
    payload = {}
    title = str(body.get("title") or "").strip()
    if title:
        payload["title"] = title
    if "description" in body:
        payload["description"] = str(body.get("description") or "").strip()[:2000]
    if "due" in body or "due_date" in body:
        due = str(body.get("due") or body.get("due_date") or "").strip()
        if due:
            payload["due_date"] = due
    if "done" in body:
        payload["done"] = bool(body.get("done"))
    if not payload:
        raise HTTPException(400, "нет полей для обновления")
    # Vikunja: обычно POST /api/v1/tasks/{id}
    for method, path in (("POST", f"/api/v1/tasks/{task_id}"), ("PUT", f"/api/v1/tasks/{task_id}")):
        try:
            res = _vikunja_json(method, path, payload)
            return JSONResponse(res if isinstance(res, dict) else {"ok": True, "task": res})
        except HTTPException as exc:
            if exc.status_code in (404, 405):
                continue
            raise
    raise HTTPException(400, "обновление задачи не поддерживается")


@router.post("/research")
async def mobile_research(request: Request):
    """Многошаговый поиск для инструмента «Исследование».

    Использует встроенный web search Open WebUI от имени пользователя:
    настройки движка/глубины задаёт администратор на /portal/search.
    """
    token = _token(request)
    _user(token)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "ожидается JSON")
    topic = str(body.get("topic") or "").strip()[:200]
    queries = [str(q).strip()[:300] for q in (body.get("queries") or []) if str(q).strip()]
    if not queries:
        raise HTTPException(400, "нужен хотя бы один поисковый запрос")
    queries = queries[:6]

    async def one(query: str):
        async with httpx.AsyncClient(timeout=90) as client:
            response = await client.post(
                f"{OWUI_URL}/api/v1/retrieval/process/web/search",
                json={"queries": [query]},
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.status_code != 200:
                return []
            return response.json() if isinstance(response.json(), list) else []

    groups = await asyncio.gather(*(one(q) for q in queries), return_exceptions=True)
    seen, lines = set(), [f"# {topic or 'Исследование'}"]
    for group in groups:
        if isinstance(group, BaseException):
            continue
        for item in group:
            if not isinstance(item, dict):
                continue
            link = str(item.get("link") or "").strip()
            if not link or link in seen:
                continue
            seen.add(link)
            title = str(item.get("title") or link).strip()
            snippet = " ".join(str(item.get("snippet") or "").split())[:350]
            lines.append(f"- [{title}]({link}) — {snippet}")
    if len(lines) == 1:
        return {"report": "По запросам ничего не найдено. Попробуйте другие формулировки."}
    lines += ["", f"Источников: {len(seen)}. Запросов: {len(queries)}."]
    return {"report": "\n".join(lines[:60])}
