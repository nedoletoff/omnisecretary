#!/usr/bin/env python3
"""Telegram-бот OmniSecretary.

Окно к тому же бэкенду Open WebUI: чаты, созданные в Telegram, видны в вебе
под тем же пользователем и наоборот (продолжение — из бота).

Ключевые свойства:
  * allowlist по Telegram user ID — всех остальных игнорируем и логируем;
  * у каждого Telegram ID свой аккаунт Open WebUI (истории не пересекаются);
  * владелец (TELEGRAM_OWNER_ID) ходит под своим аккаунтом — чаты общие с вебом;
  * приватный режим ничего не сохраняет и не читает историю;
  * long polling, внешние порты не нужны.

Зависимостей нет — только стандартная библиотека, образ python:3.12-slim.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ── Конфигурация ────────────────────────────────────────────────────────────

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
ALLOWED_IDS = {
    int(part) for part in re.split(r"[,\s]+", os.environ.get("TELEGRAM_ALLOWED_IDS", "")) if part.strip().isdigit()
}
OWNER_ID = int(os.environ["TELEGRAM_OWNER_ID"]) if os.environ.get("TELEGRAM_OWNER_ID", "").strip().isdigit() else None
ACCOUNT_MAP = {}
for pair in os.environ.get("TELEGRAM_ACCOUNT_MAP", "").split(","):
    if ":" in pair:
        tg_id, email = pair.split(":", 1)
        if tg_id.strip().isdigit():
            ACCOUNT_MAP[int(tg_id)] = email.strip()

OWUI_URL = os.environ.get("OWUI_INTERNAL_URL", "http://open-webui:8080").rstrip("/")
PUBLIC_URL = os.environ.get("PUBLIC_URL", "").rstrip("/")
OPEN_ACCESS = os.environ.get("TELEGRAM_OPEN_ACCESS", "").strip().lower() in ("1", "true", "yes")
CONFIRM_CODE = os.environ.get("TELEGRAM_CONFIRM_CODE", "").strip()
DEFAULT_MODEL = os.environ.get("TELEGRAM_DEFAULT_MODEL", "secretary")
DATA_ROOT = Path(os.environ.get("DATA_ROOT", "/srv/omnisecretary"))
SECRETS_DIR = DATA_ROOT / "secrets"
DB_PATH = Path(os.environ.get("BOT_DB_PATH", str(DATA_ROOT / "bot" / "bot.db")))
PRIVATE_HISTORY_TURNS = 6

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("omnisecretary-bot")

HELP_TEXT = """Я — OmniSecretary. Пишите текстом, я отвечу.

/new [пресет] — новый чат (secretary, omni, archivist, coder, private)
/presets — список пресетов
/model <пресет> — сменить пресет для новых чатов
/private — приватный режим: ничего не сохраняется
/public — вернуться к обычному режиму
/app — открыть веб-интерфейс (Mini App) в том же дизайне
/remind 20:30 позвонить маме — напоминание (20:30, +15m, 12.11 09:00)
/reminders — ближайшие напоминания
/forget <id> — удалить напоминание
/help — эта справка"""


# ── Telegram API ────────────────────────────────────────────────────────────


def tg(method: str, **params) -> dict:
    """Вызов Telegram Bot API. Ошибки сети не роняют процесс."""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/{method}"
    data = urllib.parse.urlencode(params).encode("utf-8")
    request = urllib.request.Request(url, data=data)
    try:
        with urllib.request.urlopen(request, timeout=70) as response:
            payload = json.loads(response.read().decode("utf-8"))
            if not payload.get("ok"):
                log.warning("telegram %s: %s", method, payload.get("description"))
            return payload
    except urllib.error.HTTPError as exc:
        log.warning("telegram %s: HTTP %s", method, exc.code)
        return {}
    except Exception as exc:  # сеть, таймаут
        log.warning("telegram %s: %s", method, exc)
        return {}


def send(chat_id: int, text: str) -> None:
    """Отправка с разбивкой по 4096 символов (лимит Telegram)."""
    text = text.strip() or "(пустой ответ)"
    for start in range(0, len(text), 3900):
        tg("sendMessage", chat_id=chat_id, text=text[start:start + 3900], disable_web_page_preview=True)


def typing(chat_id: int) -> None:
    tg("sendChatAction", chat_id=chat_id, action="typing")


# ── Open WebUI API ──────────────────────────────────────────────────────────


class OwuiError(Exception):
    pass


class Owui:
    """Минимальный клиент Open WebUI: вход, пользователи, чаты, completions."""

    def __init__(self, email: str, password: str) -> None:
        self.email = email
        self.password = password
        self.token = ""
        self._login_at = 0.0

    def _request(self, path: str, payload: dict | None = None, method: str | None = None) -> tuple[int, dict | list | str]:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(
            OWUI_URL + path, data=data, headers=headers,
            method=method or ("POST" if data is not None else "GET"),
        )
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                body = response.read().decode("utf-8", "replace")
                try:
                    return response.status, json.loads(body)
                except json.JSONDecodeError:
                    return response.status, body
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
            try:
                return exc.code, json.loads(body)
            except json.JSONDecodeError:
                return exc.code, body

    def login(self) -> None:
        status, body = self._request(
            "/api/v1/auths/signin",
            {"email": self.email, "password": self.password},
        )
        if status != 200 or not isinstance(body, dict) or not body.get("token"):
            raise OwuiError(f"вход {self.email}: HTTP {status} {body}")
        self.token = body["token"]
        self._login_at = time.time()

    def ensure_login(self) -> None:
        # JWT живёт 4 недели; обновляем раз в сутки с запасом.
        if not self.token or time.time() - self._login_at > 86400:
            self.login()

    def call(self, path: str, payload: dict | None = None, method: str | None = None):
        self.ensure_login()
        status, body = self._request(path, payload, method)
        if status == 401:
            self.login()
            status, body = self._request(path, payload, method)
        return status, body


def owner_client() -> Owui:
    """Клиент администратора: читает логин/пароль из secrets."""
    path = SECRETS_DIR / "webui-owner.txt"
    values = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip()
    email = values.get("EMAIL") or os.environ.get("WEBUI_ADMIN_EMAIL", "")
    password = values.get("PASSWORD") or os.environ.get("WEBUI_ADMIN_PASSWORD", "")
    if not email or not password:
        raise OwuiError("нет учётных данных владельца (secrets/webui-owner.txt)")
    client = Owui(email, password)
    client.login()
    return client


# ── Состояние бота ──────────────────────────────────────────────────────────


def db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS accounts (
            tg_id INTEGER PRIMARY KEY,
            email TEXT NOT NULL,
            password TEXT NOT NULL,
            owui_user_id TEXT
        );
        CREATE TABLE IF NOT EXISTS state (
            tg_id INTEGER PRIMARY KEY,
            chat_id TEXT,
            model TEXT NOT NULL,
            private INTEGER NOT NULL DEFAULT 0,
            private_messages TEXT NOT NULL DEFAULT '[]'
        );
        CREATE TABLE IF NOT EXISTS allowed (
            tg_id INTEGER PRIMARY KEY,
            added_at TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tg_id INTEGER NOT NULL,
            chat_id INTEGER NOT NULL,
            text TEXT NOT NULL,
            due_at TEXT NOT NULL,
            sent INTEGER NOT NULL DEFAULT 0
        );
        """
    )
    return conn


def is_allowed(tg_id: int) -> bool:
    """Доступ: явный allowlist, владелец или подтверждённый кодом пользователь."""
    if tg_id == OWNER_ID or tg_id in ALLOWED_IDS:
        return True
    conn = db()
    row = conn.execute("SELECT 1 FROM allowed WHERE tg_id = ?", (tg_id,)).fetchone()
    conn.close()
    return row is not None


def allow(tg_id: int) -> None:
    conn = db()
    conn.execute(
        "INSERT OR IGNORE INTO allowed (tg_id, added_at) VALUES (?, ?)",
        (tg_id, datetime.now(timezone.utc).isoformat(timespec="seconds")),
    )
    conn.commit()
    conn.close()


def allowed_list() -> list[int]:
    conn = db()
    rows = conn.execute("SELECT tg_id FROM allowed ORDER BY tg_id").fetchall()
    conn.close()
    return sorted(set(ALLOWED_IDS) | {row["tg_id"] for row in rows} |
                  ({OWNER_ID} if OWNER_ID else set()))


def get_state(tg_id: int) -> sqlite3.Row:
    conn = db()
    row = conn.execute("SELECT * FROM state WHERE tg_id = ?", (tg_id,)).fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO state (tg_id, model, private) VALUES (?, ?, 0)",
            (tg_id, DEFAULT_MODEL),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM state WHERE tg_id = ?", (tg_id,)).fetchone()
    conn.close()
    return row


def set_state(tg_id: int, **fields) -> None:
    if not fields:
        return
    conn = db()
    columns = ", ".join(f"{key} = ?" for key in fields)
    conn.execute(f"UPDATE state SET {columns} WHERE tg_id = ?", (*fields.values(), tg_id))
    conn.commit()
    conn.close()


def get_account(tg_id: int) -> sqlite3.Row | None:
    conn = db()
    row = conn.execute("SELECT * FROM accounts WHERE tg_id = ?", (tg_id,)).fetchone()
    conn.close()
    return row


def save_account(tg_id: int, email: str, password: str, user_id: str = "") -> None:
    conn = db()
    conn.execute(
        "INSERT INTO accounts (tg_id, email, password, owui_user_id) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(tg_id) DO UPDATE SET email = excluded.email, "
        "password = excluded.password, owui_user_id = excluded.owui_user_id",
        (tg_id, email, password, user_id),
    )
    conn.commit()
    conn.close()


def provision_account(tg_id: int) -> Owui:
    """Аккаунт Open WebUI для Telegram ID.

    Владелец и явные соответствия из TELEGRAM_ACCOUNT_MAP используют
    существующие учётки; для остальных создаётся отдельный пользователь,
    чтобы истории не пересекались.
    """
    mapped = ACCOUNT_MAP.get(tg_id)
    if tg_id == OWNER_ID or mapped == "owner":
        owner = owner_client()
        save_account(tg_id, owner.email, owner.password)
        return owner

    stored = get_account(tg_id)
    if stored and (mapped is None or stored["email"] == mapped):
        client = Owui(stored["email"], stored["password"])
        try:
            client.login()
            return client
        except OwuiError:
            log.warning("не удалось войти под %s, пересоздаю", stored["email"])

    email = mapped or f"tg{tg_id}@omnisecretary.local"
    password = uuid.uuid4().hex + uuid.uuid4().hex[:8]

    admin = owner_client()
    status, body = admin.call("/api/v1/auths/add", {
        "email": email,
        "password": password,
        "name": f"Telegram {tg_id}",
        "role": "user",
    })
    if status != 200:
        # Пользователь уже есть — пароль неизвестен, владельцу нужно сбросить.
        raise OwuiError(
            f"не удалось создать пользователя {email}: HTTP {status} {body}. "
            "Сбросьте пароль в Admin Panel или укажите TELEGRAM_ACCOUNT_MAP."
        )

    user_id = body.get("id", "") if isinstance(body, dict) else ""
    save_account(tg_id, email, password, user_id)
    client = Owui(email, password)
    client.login()
    return client


def client_for(tg_id: int) -> Owui:
    if tg_id == OWNER_ID:
        return owner_client()
    stored = get_account(tg_id)
    if stored is None:
        return provision_account(tg_id)
    client = Owui(stored["email"], stored["password"])
    try:
        client.login()
    except OwuiError:
        return provision_account(tg_id)
    return client


# ── Логика чата ─────────────────────────────────────────────────────────────


def new_chat(client: Owui, model: str) -> str:
    title = f"Telegram · {datetime.now(timezone.utc).astimezone().strftime('%d.%m %H:%M')}"
    status, body = client.call("/api/v1/chats/new", {
        "chat": {
            "models": [model],
            "messages": [],
            "history": {"messages": {}, "currentId": None},
            "title": title,
        },
        "folder_id": None,
    })
    if status != 200 or not isinstance(body, dict):
        raise OwuiError(f"не удалось создать чат: HTTP {status} {body}")
    return body["id"]


def ask_saved(client: Owui, chat_id: str, model: str, text: str) -> str:
    """Отправляет сообщение в сохранённый чат Open WebUI.

    Сервер сам дописывает пару «вопрос-ответ» в историю чата, поэтому
    переписка видна в веб-интерфейсе под тем же пользователем.
    """
    status, body = client.call(f"/api/v1/chats/{chat_id}")
    parent_id = None
    if status == 200 and isinstance(body, dict):
        parent_id = ((body.get("chat") or {}).get("history") or {}).get("currentId")

    user_message = {"id": str(uuid.uuid4()), "role": "user", "content": text, "models": [model]}
    if parent_id:
        user_message["parentId"] = parent_id

    status, body = client.call("/api/chat/completions", {
        "model": model,
        "chat_id": chat_id,
        "messages": [{"role": "user", "content": text}],
        "id": str(uuid.uuid4()),
        "user_message": user_message,
        "stream": False,
    })
    if status != 200:
        raise OwuiError(f"HTTP {status}: {body}")
    return (body.get("choices") or [{}])[0].get("message", {}).get("content", "") or ""


def ask_private(client: Owui, model: str, messages: list[dict]) -> str:
    """Приватный режим: запрос без chat_id — сервер ничего не сохраняет."""
    status, body = client.call("/api/chat/completions", {
        "model": model,
        "messages": messages,
        "stream": False,
    })
    if status != 200:
        raise OwuiError(f"HTTP {status}: {body}")
    return (body.get("choices") or [{}])[0].get("message", {}).get("content", "") or ""


def friendly_error(exc: Exception) -> str:
    text = str(exc)
    if "FREE_LIMIT" in text or "402" in text:
        return "Бесплатная модель сейчас на лимите (1 ответ в 45 секунд). Попробуйте через минуту."
    if "401" in text:
        return "Не удалось авторизоваться в Open WebUI. Проверьте, что аккаунт не удалён."
    return f"Ошибка: {text[:300]}"


# ── Обработчики ─────────────────────────────────────────────────────────────


def parse_when(value: str) -> datetime | None:
    """Разобрать время напоминания. Возвращает UTC или None.

    Форматы: +15m, +2h, 20:30, 12.11 09:00, 2026-11-12 09:00.
    Время без даты — ближайшее будущее (сегодня или завтра).
    """
    value = value.strip().lower()
    now = datetime.now().astimezone()
    match = re.fullmatch(r"\+(\d+)([mh])", value)
    if match:
        delta = timedelta(minutes=int(match.group(1))) if match.group(2) == "m" \
            else timedelta(hours=int(match.group(1)))
        return (now + delta).astimezone(timezone.utc)

    for fmt in ("%H:%M", "%d.%m %H:%M", "%Y-%m-%d %H:%M"):
        try:
            moment = datetime.strptime(value, fmt)
        except ValueError:
            continue
        if fmt == "%H:%M":
            moment = now.replace(hour=moment.hour, minute=moment.minute,
                                 second=0, microsecond=0)
            if moment <= now:
                moment += timedelta(days=1)
        else:
            # Год по умолчанию 1900 даёт LMT-смещение при astimezone().
            moment = moment.replace(year=now.year, tzinfo=now.tzinfo)
            if moment <= now:
                moment = moment.replace(year=now.year + 1)
        return moment.astimezone(timezone.utc)
    return None


def reminder_loop() -> None:
    """Раз в 20 секунд отправляем наступившие напоминания."""
    while True:
        try:
            conn = db()
            due = conn.execute(
                "SELECT id, chat_id, text FROM reminders "
                "WHERE sent = 0 AND due_at <= ? ORDER BY due_at",
                (datetime.now(timezone.utc).isoformat(timespec="seconds"),),
            ).fetchall()
            for row in due:
                send(row["chat_id"], f"⏰ Напоминание: {row['text']}")
                conn.execute("UPDATE reminders SET sent = 1 WHERE id = ?", (row["id"],))
            conn.commit()
            conn.close()
        except Exception:  # noqa: BLE001 — поток не должен умирать
            log.exception("напоминания")
        time.sleep(20)


def handle_command(tg_id: int, chat_id: int, text: str) -> bool:
    command, _, argument = text.partition(" ")
    command = command.split("@")[0].lower()
    argument = argument.strip()
    state = get_state(tg_id)

    if command in ("/start", "/help"):
        send(chat_id, HELP_TEXT)
        return True

    if command == "/presets":
        send(chat_id, "Пресеты: secretary, omni, archivist, coder, private.\n"
                      f"Текущий: {state['model']}")
        return True

    if command == "/model":
        if argument not in ("secretary", "omni", "archivist", "coder", "private"):
            send(chat_id, "Укажите пресет: /model secretary")
            return True
        set_state(tg_id, model=argument)
        send(chat_id, f"Пресет для новых чатов: {argument}")
        return True

    if command == "/new":
        model = argument or state["model"]
        if model not in ("secretary", "omni", "archivist", "coder", "private"):
            send(chat_id, "Неизвестный пресет. /presets — список.")
            return True
        try:
            client = client_for(tg_id)
            chat = new_chat(client, model)
        except Exception as exc:
            log.exception("новый чат")
            send(chat_id, friendly_error(exc))
            return True
        set_state(tg_id, chat_id=chat, model=model, private=0, private_messages="[]")
        send(chat_id, f"Новый чат с пресетом {model}. Спрашивайте.")
        return True

    if command == "/remind":
        when, _, what = argument.partition(" ")
        moment = parse_when(when) if when else None
        if not moment or not what.strip():
            send(chat_id, "Формат: /remind 20:30 текст\n"
                          "Ещё: +15m, +2h, 12.11 09:00, 2026-11-12 09:00")
            return True
        conn = db()
        cursor = conn.execute(
            "INSERT INTO reminders (tg_id, chat_id, text, due_at) VALUES (?, ?, ?, ?)",
            (tg_id, chat_id, what.strip(), moment.isoformat(timespec="seconds")),
        )
        conn.commit()
        conn.close()
        local = moment.astimezone()
        send(chat_id, f"Напоминание #{cursor.lastrowid} на {local:%d.%m %H:%M}: {what.strip()}")
        return True

    if command == "/reminders":
        conn = db()
        rows = conn.execute(
            "SELECT id, text, due_at FROM reminders WHERE tg_id = ? AND sent = 0 "
            "ORDER BY due_at LIMIT 10", (tg_id,),
        ).fetchall()
        conn.close()
        if not rows:
            send(chat_id, "Активных напоминаний нет.")
            return True
        lines = []
        for row in rows:
            local = datetime.fromisoformat(row["due_at"]).astimezone()
            lines.append(f"#{row['id']} {local:%d.%m %H:%M} — {row['text']}")
        send(chat_id, "\n".join(lines))
        return True

    if command == "/forget":
        if not argument.isdigit():
            send(chat_id, "Укажите номер: /forget 3")
            return True
        conn = db()
        cursor = conn.execute(
            "DELETE FROM reminders WHERE id = ? AND tg_id = ?", (int(argument), tg_id))
        conn.commit()
        conn.close()
        send(chat_id, "Удалено." if cursor.rowcount else "Такого напоминания нет.")
        return True

    if command == "/app":
        if not PUBLIC_URL:
            send(chat_id, "Публичный адрес не задан (PUBLIC_URL). "
                          "Для Mini App нужен HTTPS — см. docs/router.md.")
            return True
        if PUBLIC_URL.startswith("https://"):
            keyboard = {"inline_keyboard": [[
                {"text": "Открыть OmniSecretary", "web_app": {"url": PUBLIC_URL}}]]}
            hint = "Открываю веб-интерфейс внутри Telegram."
        else:
            keyboard = {"inline_keyboard": [[
                {"text": "Открыть в браузере", "url": PUBLIC_URL}]]}
            hint = ("Mini App требует HTTPS, поэтому откроется браузер. "
                    "Как получить HTTPS — docs/router.md.")
        tg("sendMessage", chat_id=chat_id, text=hint,
           reply_markup=json.dumps(keyboard))
        return True

    if command == "/users" and tg_id == OWNER_ID:
        ids = allowed_list()
        send(chat_id, "Доступ: " + (", ".join(str(i) for i in ids) if ids else "никого"))
        return True

    if command == "/private":
        set_state(tg_id, private=1, private_messages="[]")
        send(chat_id, "Приватный режим: сообщения не сохраняются, история и память не используются.\n"
                      "Выйти: /public")
        return True

    if command == "/public":
        set_state(tg_id, private=0, private_messages="[]", chat_id=None)
        send(chat_id, "Обычный режим. Новый чат: /new")
        return True

    return False


def handle_text(tg_id: int, chat_id: int, text: str) -> None:
    state = get_state(tg_id)
    model = state["model"]
    typing(chat_id)
    try:
        client = client_for(tg_id)
        if state["private"]:
            messages = json.loads(state["private_messages"] or "[]")
            messages.append({"role": "user", "content": text})
            answer = ask_private(client, model, messages)
            messages.append({"role": "assistant", "content": answer})
            # Держим только последние ходы: контекст не разрастается.
            messages = messages[-PRIVATE_HISTORY_TURNS * 2:]
            set_state(tg_id, private_messages=json.dumps(messages, ensure_ascii=False))
        else:
            chat = state["chat_id"] or new_chat(client, model)
            if not state["chat_id"]:
                set_state(tg_id, chat_id=chat)
            answer = ask_saved(client, chat, model, text)
        send(chat_id, answer)
    except Exception as exc:
        log.exception("ответ на сообщение")
        send(chat_id, friendly_error(exc))


# ── Цикл long polling ───────────────────────────────────────────────────────


def poll_forever() -> None:
    threading.Thread(target=reminder_loop, daemon=True).start()
    if not TELEGRAM_TOKEN:
        log.error("TELEGRAM_BOT_TOKEN не задан — бот не может работать")
        sys.exit(1)
    if not ALLOWED_IDS and not (OPEN_ACCESS and CONFIRM_CODE):
        log.error("TELEGRAM_ALLOWED_IDS пуст и открытый доступ выключен — "
                  "бот никого не пустит")
        sys.exit(1)
    log.info("бот запущен; allowlist: %s; открытый доступ: %s",
             sorted(ALLOWED_IDS), OPEN_ACCESS)
    offset = 0
    while True:
        response = tg("getUpdates", offset=offset, timeout=50, allowed_updates=json.dumps(["message"]))
        for update in response.get("result", []) or []:
            offset = max(offset, update.get("update_id", 0) + 1)
            message = update.get("message") or {}
            tg_id = (message.get("from") or {}).get("id")
            text = (message.get("text") or "").strip()
            chat_id = (message.get("chat") or {}).get("id")
            if not tg_id or not chat_id or not text:
                continue
            if not is_allowed(tg_id):
                if OPEN_ACCESS and CONFIRM_CODE:
                    if text == CONFIRM_CODE:
                        allow(tg_id)
                        send(chat_id, "Доступ открыт. Напишите /help — покажу команды.")
                        log.info("доступ по коду: id=%s", tg_id)
                    else:
                        send(chat_id, "🔒 Это приватный ассистент. "
                                      "Отправьте код подтверждения.")
                    continue
                log.warning("отказ доступа: id=%s username=%s", tg_id,
                            (message.get("from") or {}).get("username"))
                continue
            try:
                if not handle_command(tg_id, chat_id, text):
                    handle_text(tg_id, chat_id, text)
            except Exception:
                log.exception("обработка сообщения")
                send(chat_id, "Внутренняя ошибка. Подробности в логах контейнера.")
        time.sleep(0.5)


if __name__ == "__main__":
    poll_forever()
