"""Утренний брифинг и напоминания в Telegram.

Почему отдельный контейнер, а не плагин Open WebUI: расписание должно
работать, даже когда никто не открыл чат, а падение планировщика не должно
ронять ядро. Зависимостей нет — только stdlib, поэтому образ ~80 МБ.

Источники:
- погода: api.met.no (бесплатно, без ключа; open-meteo недоступен из сети Pi);
- календарь: Open WebUI `/api/v1/calendars/events`;
- задачи: Vikunja `/api/v1/tasks?filter=done=false`;
- заметки: количество новых файлов в `AI/Inbox` вольта (если подключён).
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import FastAPI
from fastapi.responses import JSONResponse

TZ = ZoneInfo(os.environ.get("BRIEFING_TZ", "Europe/Moscow"))
BRIEFING_TIME = os.environ.get("BRIEFING_TIME", "07:30")
STATE_FILE = Path(os.environ.get("BRIEFING_STATE", "/state/last-briefing"))
# Настройки из бота (том bot смонтирован только для чтения).
SETTINGS_FILE = Path(os.environ.get("BRIEFING_SETTINGS", "/data/bot/briefing.json"))
ALL_SECTIONS = ("weather", "calendar", "tasks", "notes")
VAULT_DIR = Path(os.environ.get("VAULT_DIR", "/vault"))

WEBUI_URL = os.environ.get("WEBUI_BASE_URL", "http://open-webui:8080").rstrip("/")
WEBUI_KEY = os.environ.get("WEBUI_API_KEY", "")
VIKUNJA_URL = os.environ.get("VIKUNJA_BASE_URL", "http://vikunja:3456").rstrip("/")
VIKUNJA_TOKEN = os.environ.get("VIKUNJA_API_TOKEN", "")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT = os.environ.get("TELEGRAM_OWNER_ID", "")
WEATHER_LAT = os.environ.get("WEATHER_LAT", "")
WEATHER_LON = os.environ.get("WEATHER_LON", "")
WEATHER_CITY = os.environ.get("WEATHER_CITY", "дом")

SYMBOLS = {
    "clearsky": "ясно", "fair": "малооблачно", "partlycloudy": "переменная облачность",
    "cloudy": "облачно", "fog": "туман", "lightrain": "небольшой дождь",
    "rain": "дождь", "heavyrain": "сильный дождь", "lightrainshowers": "ливни",
    "rainshowers": "ливни", "heavyrainshowers": "сильные ливни",
    "lightsleet": "небольшой мокрый снег", "sleet": "мокрый снег",
    "lightsnow": "небольшой снег", "snow": "снег", "heavysnow": "сильный снег",
    "lightsnowshowers": "снегопады", "snowshowers": "снегопады",
    "thunderstorm": "гроза",
}

app = FastAPI(title="OmniSecretary Proactive", version="1.0")
_last_error = ""


def http_json(url: str, headers: dict | None = None,
              payload: dict | None = None, timeout: int = 30):
    data = None
    headers = dict(headers or {})
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode())


def owner_place() -> tuple[str, str, str]:
    """Локация владельца из настроек бота (если задана), иначе env."""
    lat, lon, city = WEATHER_LAT, WEATHER_LON, WEATHER_CITY
    db_path = os.environ.get("BOT_DB_PATH", "")
    owner = os.environ.get("TELEGRAM_OWNER_ID", "").strip()
    if db_path and owner.isdigit() and os.path.exists(db_path):
        try:
            import sqlite3
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            row = conn.execute(
                "SELECT city, lat, lon FROM settings WHERE tg_id = ?",
                (int(owner),)).fetchone()
            conn.close()
            if row:
                city, lat, lon = row[0], str(row[1]), str(row[2])
        except Exception:  # noqa: BLE001 — брифинг не должен падать
            pass
    return lat, lon, city


def weather_text() -> str:
    lat, lon, city = owner_place()
    if not lat or not lon:
        return "Погода: не настроена (WEATHER_LAT/WEATHER_LON)."
    url = ("https://api.met.no/weatherapi/locationforecast/2.0/compact"
           f"?lat={lat}&lon={lon}")
    try:
        data = http_json(url, {"User-Agent": "OmniSecretary/1.0 (self-hosted)"})
    except Exception as error:  # noqa: BLE001 — брифинг не должен падать
        return f"Погода: недоступна ({type(error).__name__})."

    series = data["properties"]["timeseries"]
    current = series[0]["data"]["instant"]["details"]["air_temperature"]
    temps = [point["data"]["instant"]["details"]["air_temperature"]
             for point in series[:12]]
    symbol = ""
    precipitation = 0.0
    for point in series[:12]:
        details = point["data"].get("next_1_hours") or point["data"].get("next_6_hours")
        if details:
            symbol = details.get("summary", {}).get("symbol_code", "")
            precipitation += float(details.get("details", {}).get("precipitation_amount", 0) or 0)
    kind = SYMBOLS.get(symbol.split("_")[0], symbol or "без данных")
    return (f"Погода ({city}): сейчас {current:+.0f} °C, {kind}; "
            f"за 12 ч от {min(temps):+.0f} до {max(temps):+.0f} °C, "
            f"осадки {precipitation:.1f} мм.")


def events_text() -> str:
    if not WEBUI_KEY:
        return "Календарь: не настроен (WEBUI_API_KEY)."
    now = datetime.now(TZ)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    query = urllib.parse.urlencode({"start": start.isoformat(), "end": end.isoformat()})
    try:
        events = http_json(f"{WEBUI_URL}/api/v1/calendars/events?{query}",
                           {"Authorization": f"Bearer {WEBUI_KEY}"})
    except Exception as error:  # noqa: BLE001
        return f"Календарь: недоступен ({type(error).__name__})."
    if not events:
        return "Календарь: на сегодня событий нет."
    lines = []
    for event in events[:10]:
        title = event.get("title") or "без названия"
        # Open WebUI хранит время в наносекундах.
        raw = int(event.get("start_at") or 0)
        if event.get("all_day") or not raw:
            moment = "весь день"
        else:
            moment = datetime.fromtimestamp(raw / 1e9, TZ).strftime("%H:%M")
        lines.append(f"— {moment} {title}")
    return "Календарь:\n" + "\n".join(lines)


def tasks_text() -> str:
    if not VIKUNJA_TOKEN:
        return "Задачи: Vikunja не настроена."
    try:
        tasks = http_json(f"{VIKUNJA_URL}/api/v1/tasks?filter=done%20%3D%20false&per_page=50",
                          {"Authorization": f"Bearer {VIKUNJA_TOKEN}"})
    except Exception as error:  # noqa: BLE001
        return f"Задачи: недоступны ({type(error).__name__})."

    today = datetime.now(TZ).date()
    due_today, overdue = [], []
    for task in tasks:
        due = task.get("due_date") or ""
        title = task.get("title") or "без названия"
        if not due:
            continue
        try:
            moment = datetime.fromisoformat(due.replace("Z", "+00:00")).astimezone(TZ).date()
        except ValueError:
            continue
        if moment < today:
            overdue.append(f"— {title} (просрочено {moment:%d.%m})")
        elif moment == today:
            due_today.append(f"— {title}")
    if not due_today and not overdue:
        return "Задачи: на сегодня сроков нет."
    parts = []
    if overdue:
        parts.append("Просрочено:\n" + "\n".join(overdue[:5]))
    if due_today:
        parts.append("Сегодня:\n" + "\n".join(due_today[:8]))
    return "Задачи:\n" + "\n".join(parts)


def notes_text() -> str:
    inbox = VAULT_DIR / "AI" / "Inbox"
    if not inbox.is_dir():
        return ""
    notes = sorted(inbox.glob("*.md"))
    if not notes:
        return "Заметки: Inbox пуст."
    return f"Заметки: в AI/Inbox {len(notes)} шт., последняя — {notes[-1].name}."


def settings() -> dict:
    """Время, разделы и вкл/выкл: файл из бота важнее env."""
    data = {"enabled": True, "time": BRIEFING_TIME, "sections": list(ALL_SECTIONS)}
    try:
        raw = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return data
    if isinstance(raw.get("enabled"), bool):
        data["enabled"] = raw["enabled"]
    time_value = str(raw.get("time", ""))
    if len(time_value) == 5 and time_value[2] == ":" and time_value[:2].isdigit() \
            and time_value[3:].isdigit():
        data["time"] = time_value
    sections = raw.get("sections")
    if isinstance(sections, list):
        data["sections"] = [s for s in sections if s in ALL_SECTIONS]
    return data


def build_briefing() -> str:
    date = datetime.now(TZ)
    chosen = settings()["sections"]
    header = f"Доброе утро. {date:%d.%m.%Y}, {date:%H:%M}."
    builders = {"weather": weather_text, "calendar": events_text,
                "tasks": tasks_text, "notes": notes_text}
    parts = [header]
    for name in ALL_SECTIONS:
        if name not in chosen:
            continue
        text = builders[name]()
        if text:
            parts.append(text)
    return "\n\n".join(parts)


def send_telegram(text: str) -> bool:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT:
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        http_json(url, payload={"chat_id": TELEGRAM_CHAT, "text": text,
                                "disable_web_page_preview": True}, timeout=20)
        return True
    except Exception as error:  # noqa: BLE001
        global _last_error
        _last_error = f"{type(error).__name__}: {error}"
        return False


def _already_sent(day: str) -> bool:
    try:
        return STATE_FILE.read_text().strip() == day
    except OSError:
        return False


def _mark_sent(day: str) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(day)


CATCHUP_MINUTES = int(os.environ.get("BRIEFING_CATCHUP_MINUTES", "180"))


def _due(now: datetime) -> bool:
    """Пора ли отправлять: время наступило и окно догона не истекло.

    Догон нужен, если Pi был выключен в 07:30: при старте в 08:10 брифинг
    уйдёт, а в 20:00 — уже нет (не «доброе утро» среди ночи).
    """
    hour, minute = (int(part) for part in settings()["time"].split(":"))
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    delay = (now - target).total_seconds() / 60
    return 0 <= delay <= CATCHUP_MINUTES


def scheduler_loop() -> None:
    """Каждые 30 с проверяем, не пора ли отправить брифинг."""
    while True:
        try:
            now = datetime.now(TZ)
            current = settings()
            if current["enabled"] and _due(now) and not _already_sent(now.date().isoformat()):
                if send_telegram(build_briefing()):
                    _mark_sent(now.date().isoformat())
        except Exception:  # noqa: BLE001 — цикл не должен умирать
            pass
        time.sleep(30)


@app.on_event("startup")
def start_scheduler() -> None:
    threading.Thread(target=scheduler_loop, daemon=True).start()


@app.get("/healthz")
def healthz() -> JSONResponse:
    return JSONResponse({
        "status": "ok",
        "briefing_time": settings()["time"],
        "sections": settings()["sections"],
        "enabled": settings()["enabled"],
        "catchup_minutes": CATCHUP_MINUTES,
        "timezone": str(TZ),
        "telegram": bool(TELEGRAM_TOKEN and TELEGRAM_CHAT),
        "last_error": _last_error,
    })


@app.get("/briefing")
def briefing(send: int = 0) -> JSONResponse:
    text = build_briefing()
    delivered = send_telegram(text) if send else False
    return JSONResponse({"text": text, "sent": delivered})
