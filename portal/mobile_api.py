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


# ── Вольт Obsidian (чтение/поиск, только для владельца) ─────────────────

VAULT_DIR = Path(os.environ.get("VAULT_DIR", "/vault")).resolve()
VAULT_INDEX = Path(os.environ.get("VAULT_INDEX_PATH", "/vault-index/vault.db"))


def _require_owner(user: dict) -> None:
    if not _is_owner(user):
        raise HTTPException(403, "вольт доступен только владельцу")


def _safe_vault_path(rel: str) -> Path:
    p = (VAULT_DIR / rel).resolve()
    if VAULT_DIR not in p.parents and p != VAULT_DIR:
        raise HTTPException(400, "недопустимый путь")
    return p


@router.get("/vault/files")
def vault_files(request: Request, q: str = "", limit: int = 50) -> JSONResponse:
    token = _token(request)
    _require_owner(_user(token))
    if not VAULT_DIR.is_dir():
        return JSONResponse({"files": [], "note": "вольт не смонтирован"})
    limit = max(1, min(limit, 100))
    files = []
    for path in sorted(VAULT_DIR.rglob("*.md")):
        if ".git" in path.parts:
            continue
        rel = str(path.relative_to(VAULT_DIR))
        if q and q.lower() not in rel.lower():
            continue
        files.append({"path": rel, "size": path.stat().st_size})
        if len(files) >= limit:
            break
    return JSONResponse({"files": files})


@router.get("/vault/read")
def vault_read(request: Request, path: str) -> JSONResponse:
    token = _token(request)
    _require_owner(_user(token))
    target = _safe_vault_path(path)
    if not target.is_file():
        raise HTTPException(404, "файл не найден")
    if target.suffix.lower() != ".md":
        raise HTTPException(400, "можно читать только .md")
    text = target.read_text(encoding="utf-8", errors="replace")
    if len(text) > 20000:
        text = text[:20000] + "\n…(обрезано)"
    return JSONResponse({"path": path, "content": text})


@router.get("/vault/search")
def vault_search(request: Request, q: str, limit: int = 20) -> JSONResponse:
    token = _token(request)
    _require_owner(_user(token))
    query = (q or "").strip()
    if not query:
        raise HTTPException(400, "нужен q")
    limit = max(1, min(limit, 50))
    results = []
    # Пытаемся FTS5-индекс, иначе простой поиск по файлам.
    if VAULT_INDEX.is_file():
        try:
            conn = sqlite3.connect(f"file:{VAULT_INDEX}?mode=ro", uri=True, timeout=5)
            rows = conn.execute(
                "SELECT path, snippet(notes, 1, '', '', ' … ', 20) FROM notes "
                "WHERE notes MATCH ? LIMIT ?", (query, limit)).fetchall()
            conn.close()
            for path, snippet in rows:
                results.append({"path": path, "snippet": snippet or ""})
            return JSONResponse({"results": results, "engine": "fts"})
        except sqlite3.Error:
            pass
    # Fallback: grep-поиск.
    needle = query.lower()
    for path in VAULT_DIR.rglob("*.md") if VAULT_DIR.is_dir() else []:
        if ".git" in path.parts:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        low = text.lower()
        idx = low.find(needle)
        if idx == -1:
            continue
        start = max(0, idx - 40)
        end = min(len(text), idx + len(query) + 80)
        snippet = text[start:end].replace("\n", " ")
        results.append({"path": str(path.relative_to(VAULT_DIR)), "snippet": snippet})
        if len(results) >= limit:
            break
    return JSONResponse({"results": results, "engine": "scan"})

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
