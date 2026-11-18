"""Мобильный API OmniSecretary.

Клиент приложения ходит сюда с Bearer-токеном Open WebUI (тем же, что
получает при входе). Портал проверяет токен в Open WebUI и работает от
имени пользователя: чужие чаты, календари и заметки недоступны.

Зачем отдельный слой, если есть API Open WebUI: голос, агрегат «Сегодня»,
запись в вольт и напоминания в Open WebUI отсутствуют или требуют
админских прав.
"""

from __future__ import annotations

import base64
import json
import os
import sqlite3
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, Response

OWUI_URL = os.environ.get("OWUI_URL", "http://open-webui:8080").rstrip("/")
SECRETS_DIR = Path(os.environ.get("SECRETS_DIR", "/secrets"))
BOT_DB_PATH = Path(os.environ.get("BOT_DB_PATH", "/bot/bot.db"))
STATUS_FILE = Path(os.environ.get("STATUS_FILE", "/status/device.json"))
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


def _vault_auth() -> tuple[str, str]:
    path = SECRETS_DIR / "vault-admin.txt"
    values = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip()
    return values.get("USERNAME", "owner"), values.get("PASSWORD", "")


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

@router.get("/today")
def today(request: Request) -> JSONResponse:
    token = _token(request)
    _user(token)
    city, lat, lon = _place()
    return JSONResponse({
        "place": city,
        "weather": _weather(lat, lon),
        "events": _events(token),
        "tasks": _tasks(),
        "notes": _notes(token),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })


@router.post("/voice")
async def voice(request: Request, file: UploadFile = File(...)) -> JSONResponse:
    token = _token(request)
    _user(token)
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "пустой файл")
    if len(raw) > 10 * 1024 * 1024:
        raise HTTPException(413, "аудио больше 10 МБ")
    try:
        with httpx.Client(timeout=120) as client:
            response = client.post(
                f"{STT_URL}/v1/audio/transcriptions",
                files={"file": (file.filename or "audio.m4a", raw,
                                file.content_type or "audio/mp4")},
                data={"model": "vosk", "language": "ru"})
    except httpx.HTTPError:
        raise HTTPException(503, "распознавание недоступно")
    if response.status_code != 200:
        raise HTTPException(502, "ошибка распознавания")
    return JSONResponse({"text": response.json().get("text", "")})


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
    payload = {"model": "tts", "input": text, "voice": data.get("voice") or "baya"}
    try:
        with httpx.Client(timeout=120) as client:
            response = client.post(f"{TTS_URL}/v1/audio/speech", json=payload)
    except httpx.HTTPError:
        raise HTTPException(503, "синтез речи недоступен")
    if response.status_code != 200:
        raise HTTPException(502, "ошибка синтеза речи")
    return Response(content=response.content, media_type="audio/wav")


@router.post("/note")
async def note(request: Request) -> JSONResponse:
    token = _token(request)
    _user(token)
    data = await request.json()
    text = str(data.get("text") or "").strip()
    if not text:
        raise HTTPException(400, "пустой текст")
    user, password = _vault_auth()
    if not password:
        raise HTTPException(503, "вольт не настроен")
    payload = {"title": str(data.get("title") or "").strip(), "text": text,
               "folder": str(data.get("folder") or "AI/Inbox")}
    try:
        with httpx.Client(timeout=180, auth=(user, password)) as client:
            response = client.post(f"{VAULT_URL}/api/note", json=payload)
    except httpx.HTTPError:
        raise HTTPException(503, "вольт недоступен")
    if response.status_code != 200:
        detail = ""
        try:
            detail = str(response.json().get("detail", ""))
        except Exception:  # noqa: BLE001
            detail = response.text[:120]
        raise HTTPException(response.status_code, detail or "не удалось записать заметку")
    return JSONResponse(response.json())


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
