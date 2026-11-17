"""Портал OmniSecretary: регистрация по инвайт-токену, вход и личные настройки.

Живёт за nginx по пути /portal/. Аккаунты создаются в Open WebUI через
админский API, поэтому пользователь получает тот же чат, память и пресеты.
Личные данные (город, GitHub-токен, пресет) хранятся в portal.db.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import sqlite3
import threading
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

OWUI_URL = os.environ.get("OWUI_URL", "http://open-webui:8080").rstrip("/")
SECRETS_DIR = Path(os.environ.get("SECRETS_DIR", "/secrets"))
DATA_DIR = Path(os.environ.get("PORTAL_DATA", "/data"))
DB_PATH = DATA_DIR / "portal.db"
BOT_DB_PATH = Path(os.environ.get("BOT_DB_PATH", "/bot/bot.db"))
SECRET = os.environ.get("PORTAL_SECRET", "").strip()
# Без настоящего секрета подпись cookie предсказуема: лучше не стартовать.
if len(SECRET) < 32 or SECRET in {"change-me", "changeme", "secret"}:
    raise RuntimeError(
        "PORTAL_SECRET не задан или слишком короткий (нужно ≥32 символа). "
        "Сгенерируйте: openssl rand -hex 32")
OWNER = os.environ.get("PORTAL_OWNER", "").strip()
STATUS_FILE = Path(os.environ.get("STATUS_FILE", "/status/device.json"))
TOKEN_KEY_FILE = DATA_DIR / "token.key"
INVITE_TOKEN = (os.environ.get("REGISTER_TOKEN")
                or os.environ.get("TELEGRAM_CONFIRM_CODE") or "")
DEFAULT_CITY = os.environ.get("WEATHER_CITY", "")
WEATHER_UA = "OmniSecretary/1.0 (self-hosted)"
SESSION_TTL = 7 * 24 * 3600
RESERVED = {"admin", "owner", "root", "system", "administrator"}
USERNAME_RE = re.compile(r"^[a-zA-Z0-9._-]{3,32}$")
PRESETS = ("secretary", "omni", "archivist", "coder", "private")

app = FastAPI(title="OmniSecretary Portal", version="1.0")

# Таймауты на ввод пароля/токена: после 5 ошибок за 15 минут
# ключ блокируется на 15 минут. Не лимит запросов, а пауза.
_guard_lock = threading.Lock()
_failures: dict[str, list[float]] = {}
_blocked: dict[str, float] = {}
FAIL_WINDOW = 15 * 60
FAIL_LIMIT = 5
FAIL_COOLDOWN = 15 * 60
_admin_lock = threading.Lock()
_admin_cache: dict[str, object] = {"token": "", "expires": 0.0}


# ── Общее ────────────────────────────────────────────────────────────────

def db() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS profiles (
            owui_id TEXT PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            city TEXT NOT NULL DEFAULT '',
            github_token TEXT NOT NULL DEFAULT '',
            preset TEXT NOT NULL DEFAULT 'secretary',
            telegram_id INTEGER,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS registrations (
            ip TEXT NOT NULL,
            created_at REAL NOT NULL
        );
        """
    )
    return conn


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ── Шифрование пользовательских токенов ──────────────────────────────────
# GitHub-токен в portal.db хранится в AES-256-GCM. Ключ — в /data/token.key
# (600). Без ключа расшифровать дамп базы нельзя.

_token_key_cache: bytes | None = None


def _token_key() -> bytes:
    global _token_key_cache
    if _token_key_cache is None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        if TOKEN_KEY_FILE.exists():
            raw = TOKEN_KEY_FILE.read_bytes()
            _token_key_cache = (raw[:32] if len(raw) >= 32
                                else raw.ljust(32, b"\0"))
        else:
            key = os.urandom(32)
            old_umask = os.umask(0o077)
            try:
                TOKEN_KEY_FILE.write_bytes(key)
            finally:
                os.umask(old_umask)
            TOKEN_KEY_FILE.chmod(0o600)
            _token_key_cache = key
    return _token_key_cache


def enc_token(value: str) -> str:
    value = (value or "").strip()
    if not value or value.startswith("enc:v1:"):
        return value
    nonce = os.urandom(12)
    blob = AESGCM(_token_key()).encrypt(nonce, value.encode(), b"github")
    return "enc:v1:" + base64.urlsafe_b64encode(nonce).decode() + \
        ":" + base64.urlsafe_b64encode(blob).decode()


def dec_token(value: str) -> str:
    if not value:
        return ""
    if not value.startswith("enc:v1:"):
        return value  # запись до миграции
    try:
        _, _, nonce_b64, blob_b64 = value.split(":", 3)
        nonce = base64.urlsafe_b64decode(nonce_b64)
        blob = base64.urlsafe_b64decode(blob_b64)
        return AESGCM(_token_key()).decrypt(nonce, blob, b"github").decode()
    except Exception:  # noqa: BLE001 — битый ключ/данные не должны ронять портал
        return ""


def migrate_tokens() -> int:
    """Шифрует токены, сохранённые открытым текстом до этой версии."""
    conn = db()
    rows = conn.execute(
        "SELECT owui_id, github_token FROM profiles WHERE github_token != ''"
    ).fetchall()
    changed = 0
    for row in rows:
        value = row["github_token"]
        if not value.startswith("enc:v1:"):
            conn.execute("UPDATE profiles SET github_token = ? WHERE owui_id = ?",
                         (enc_token(value), row["owui_id"]))
            changed += 1
    if changed:
        conn.commit()
    conn.close()
    return changed


def sign(payload: str) -> str:
    digest = hmac.new(SECRET.encode(), payload.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def make_session(owui_id: str, username: str, token: str = "") -> str:
    data = json.dumps({"uid": owui_id, "u": username, "t": token,
                       "exp": int(time.time()) + SESSION_TTL})
    body = base64.urlsafe_b64encode(data.encode()).decode().rstrip("=")
    return f"{body}.{sign(body)}"


def read_session(request: Request) -> dict | None:
    raw = request.cookies.get("portal_session", "")
    if "." not in raw:
        return None
    body, signature = raw.rsplit(".", 1)
    if not hmac.compare_digest(sign(body), signature):
        return None
    try:
        data = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    except Exception:  # noqa: BLE001
        return None
    if int(data.get("exp", 0)) < int(time.time()):
        return None
    return data


def csrf_token() -> str:
    return sign(f"csrf:{int(time.time()) // 3600}")


def csrf_ok(value: str) -> bool:
    return hmac.compare_digest(value, csrf_token()) or hmac.compare_digest(
        value, sign(f"csrf:{int(time.time()) // 3600 - 1}"))


def client_ip(request: Request) -> str:
    """IP только из X-Real-IP: его ставит nginx, клиент его не подделает."""
    return (request.headers.get("x-real-ip")
            or (request.client.host if request.client else "")).strip()


def guard_left(key: str) -> int:
    """Сколько секунд осталось до конца таймаута (0 — можно пробовать)."""
    now = time.time()
    with _guard_lock:
        until = _blocked.get(key, 0.0)
        return max(0, int(until - now))


def guard_fail(key: str) -> None:
    now = time.time()
    with _guard_lock:
        attempts = [s for s in _failures.get(key, []) if s > now - FAIL_WINDOW]
        attempts.append(now)
        _failures[key] = attempts
        if len(attempts) >= FAIL_LIMIT:
            _blocked[key] = now + FAIL_COOLDOWN
            _failures[key] = []


def guard_reset(key: str) -> None:
    with _guard_lock:
        _failures.pop(key, None)
        _blocked.pop(key, None)


def esc(value: object) -> str:
    return (str(value or "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def page(title: str, body: str, message: str = "", error: bool = False,
         wide: bool = False) -> str:
    notice = ""
    if message:
        color = "#fb4934" if error else "#b8bb26"
        notice = f'<div class="notice" style="border-color:{color}">{esc(message)}</div>'
    return f"""<!doctype html>
<html lang="ru"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)} — OmniSecretary</title>
<style>
:root {{ --bg:#282828; --bg1:#3c3836; --bg2:#504945; --fg:#ebdbb2;
        --gray:#928374; --yellow:#fabd2f; --green:#b8bb26; --red:#fb4934; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; min-height:100vh; background:var(--bg); color:var(--fg);
       font:16px/1.5 ui-monospace,"JetBrains Mono",monospace;
       display:flex; align-items:center; justify-content:center; padding:24px; }}
.card {{ width:100%; max-width:440px; background:var(--bg1);
        border:1px solid var(--bg2); border-radius:14px; padding:28px; }}
.wide .card {{ max-width:980px; }}
h1 {{ font-size:20px; margin:0 0 4px; }}
h1 span {{ color:var(--yellow); }}
p.sub {{ color:var(--gray); margin:0 0 20px; font-size:13px; }}
label {{ display:block; font-size:13px; color:var(--gray); margin:14px 0 6px; }}
input,select {{ width:100%; padding:10px 12px; border-radius:8px;
  border:1px solid var(--bg2); background:var(--bg); color:var(--fg);
  font:inherit; }}
input:focus,select:focus {{ outline:none; border-color:var(--yellow); }}
button {{ margin-top:20px; width:100%; padding:11px; border:0; border-radius:8px;
  background:var(--yellow); color:#282828; font:inherit; font-weight:700;
  cursor:pointer; }}
button:hover {{ filter:brightness(1.08); }}
.notice {{ margin:0 0 16px; padding:10px 12px; border-left:3px solid var(--green);
  background:var(--bg); font-size:13px; }}
a {{ color:var(--yellow); }}
.foot {{ margin-top:18px; font-size:12px; color:var(--gray); text-align:center; }}
</style></head><body{" class=\"wide\"" if wide else ""}><div class="card">
<h1><span>Omni</span>Secretary</h1>
<p class="sub">{esc(title)}</p>{notice}{body}
</div></body></html>"""


def secrets() -> tuple[str, str]:
    path = SECRETS_DIR / "webui-owner.txt"
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values.get("EMAIL", "nedoletoff"), values.get("PASSWORD", "")


def admin_token() -> str:
    with _admin_lock:
        if _admin_cache["token"] and float(_admin_cache["expires"]) > time.time():
            return str(_admin_cache["token"])
        email, password = secrets()
        with httpx.Client(timeout=20) as client:
            response = client.post(f"{OWUI_URL}/api/v1/auths/signin",
                                   json={"email": email, "password": password})
            response.raise_for_status()
            token = response.json().get("token", "")
        _admin_cache["token"] = token
        _admin_cache["expires"] = time.time() + 600
        return token


def owui_email(username: str) -> str:
    return username if "@" in username else f"{username}@omnisecretary.local"


def create_owui_user(username: str, password: str) -> tuple[str, str]:
    """Создаёт пользователя в Open WebUI. Возвращает (id, ошибка)."""
    token = admin_token()
    with httpx.Client(timeout=30) as client:
        response = client.post(
            f"{OWUI_URL}/api/v1/auths/add",
            headers={"Authorization": f"Bearer {token}"},
            json={"name": username, "email": owui_email(username),
                  "password": password, "role": "user"},
        )
    if response.status_code == 200:
        return response.json().get("id", ""), ""
    detail = ""
    try:
        detail = str(response.json().get("detail", ""))
    except Exception:  # noqa: BLE001
        detail = response.text[:120]
    if "taken" in detail.lower() or response.status_code in (400, 409):
        return "", "Логин уже занят"
    return "", f"Open WebUI: {detail or response.status_code}"


def owui_login(username: str, password: str) -> tuple[str, str, str]:
    # Владелец входит по логину без домена (nedoletoff), остальные — по
    # username@omnisecretary.local. Пробуем оба варианта, чтобы вход работал
    # и для аккаунтов, созданных вручную в админке.
    candidates = [username]
    mapped = owui_email(username)
    if mapped != username:
        candidates.append(mapped)
    with httpx.Client(timeout=20) as client:
        for email in candidates:
            response = client.post(f"{OWUI_URL}/api/v1/auths/signin",
                                   json={"email": email, "password": password})
            if response.status_code == 200:
                data = response.json()
                return data.get("token", ""), data.get("id", ""), ""
    return "", "", "Неверный логин или пароль"


# ── Страницы ─────────────────────────────────────────────────────────────

@app.get("/healthz")
def healthz() -> Response:
    return Response("ok\n", media_type="text/plain")


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return RedirectResponse("/portal/login", status_code=303)


@app.get("/register", response_class=HTMLResponse)
def register_form(request: Request, message: str = "", error: int = 0):
    body = f"""
<form method="post" action="/portal/register">
  <input type="hidden" name="csrf" value="{csrf_token()}">
  <label>Логин</label>
  <input name="username" required minlength="3" maxlength="32"
         pattern="[a-zA-Z0-9._-]+" autocomplete="username" autofocus>
  <label>Пароль</label>
  <input type="password" name="password" required minlength="8"
         autocomplete="new-password">
  <label>Пароль ещё раз</label>
  <input type="password" name="password2" required minlength="8"
         autocomplete="new-password">
  <label>Токен приглашения</label>
  <input name="token" required autocomplete="off">
  <button type="submit">Создать аккаунт</button>
</form>
<p class="foot">Уже есть аккаунт? <a href="/portal/login">Войти</a></p>"""
    return HTMLResponse(page("Регистрация", body, message, bool(error)))


@app.post("/register", response_class=HTMLResponse)
def register(request: Request, username: str = Form(""), password: str = Form(""),
             password2: str = Form(""), token: str = Form(""), csrf: str = Form("")):
    ip = client_ip(request)
    key = f"reg:{ip}"

    def fail(text: str):
        guard_fail(key)
        return HTMLResponse(page("Регистрация", "", text, True), status_code=400)

    left = guard_left(key)
    if left:
        return HTMLResponse(
            page("Регистрация", "",
                 f"Слишком много ошибок. Повторите через {left // 60 + 1} мин.", True),
            status_code=429)
    if not csrf_ok(csrf):
        return fail("Форма устарела — обновите страницу")
    username = username.strip().lower()
    if not USERNAME_RE.match(username) or username in RESERVED:
        return fail("Логин: 3–32 символа, латиница, цифры, точка, дефис, подчёркивание")
    if len(password) < 8:
        return fail("Пароль должен быть не короче 8 символов")
    if password != password2:
        return fail("Пароли не совпадают")
    if not INVITE_TOKEN or not hmac.compare_digest(token.strip(), INVITE_TOKEN):
        time.sleep(0.5)
        return fail("Неверный токен приглашения")

    owui_id, error = create_owui_user(username, password)
    if error:
        return fail(error)
    guard_reset(key)

    conn = db()
    try:
        conn.execute(
            "INSERT INTO profiles (owui_id, username, city, preset, created_at) "
            "VALUES (?, ?, ?, 'secretary', ?)",
            (owui_id, username, DEFAULT_CITY, now_iso()))
        conn.commit()
    finally:
        conn.close()

    body = """
<p class="sub">Аккаунт создан. Теперь войдите под своим логином.</p>
<p><a href="/portal/login">Перейти ко входу</a></p>
<p class="foot">Тот же логин и пароль работают в чате Open WebUI.</p>"""
    return HTMLResponse(page("Готово", body))


@app.get("/login", response_class=HTMLResponse)
def login_form(message: str = "", error: int = 0):
    body = """
<form method="post" action="/portal/login">
  <label>Логин</label>
  <input name="username" required autocomplete="username" autofocus>
  <label>Пароль</label>
  <input type="password" name="password" required autocomplete="current-password">
  <button type="submit">Войти</button>
</form>
<p class="foot">Нет аккаунта? <a href="/portal/register">Зарегистрироваться</a></p>"""
    return HTMLResponse(page("Вход", body, message, bool(error)))


@app.post("/login", response_class=HTMLResponse)
def login(request: Request, username: str = Form(""), password: str = Form("")):
    username = username.strip().lower()
    key = f"login:{client_ip(request)}:{username}"
    left = guard_left(key)
    if left:
        return HTMLResponse(
            page("Вход", "",
                 f"Слишком много неверных попыток. Подождите {left // 60 + 1} мин.", True),
            status_code=429)
    token, owui_id, error = owui_login(username, password)
    if error:
        time.sleep(0.5)
        guard_fail(key)
        left = guard_left(key)
        if left:
            error = f"Слишком много неверных попыток. Подождите {left // 60 + 1} мин."
        return HTMLResponse(page("Вход", "", error, True), status_code=400)
    guard_reset(key)

    # Профиль мог быть создан в обход портала (например, админом).
    conn = db()
    exists = conn.execute("SELECT 1 FROM profiles WHERE owui_id = ?",
                          (owui_id,)).fetchone()
    if not exists:
        conn.execute(
            "INSERT OR IGNORE INTO profiles (owui_id, username, city, preset, "
            "created_at) VALUES (?, ?, ?, 'secretary', ?)",
            (owui_id, username, DEFAULT_CITY, now_iso()))
        conn.commit()
    conn.close()

    # Токен кладём в localStorage — так его видит фронтенд Open WebUI
    # (портал и чат живут на одном origin).
    body = f"""
<p class="sub">Вход выполнен, открываем чат…</p>
<script>
localStorage.setItem("token", {json.dumps(token)});
location.replace("/");
</script>
<noscript><a href="/">Открыть OmniSecretary</a></noscript>"""
    response = HTMLResponse(page("Вход", body))
    response.set_cookie("portal_session", make_session(owui_id, username, token),
                        max_age=SESSION_TTL, httponly=True, samesite="lax")
    return response


def owui_call(token: str, method: str, path: str,
              params: dict | None = None, body: dict | None = None):
    """Запрос в Open WebUI от имени пользователя (его JWT из сессии)."""
    with httpx.Client(timeout=25) as client:
        response = client.request(method, OWUI_URL + path, params=params,
                                  json=body,
                                  headers={"Authorization": f"Bearer {token}"})
        response.raise_for_status()
        return response.json() if response.content else {}


def quick_page(session: dict, message: str = "", error: bool = False) -> HTMLResponse:
    token = session.get("t", "")
    if not token:
        return RedirectResponse("/portal/login", status_code=303)
    today = datetime.now()
    start = today.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=30)
    try:
        events = owui_call(token, "GET", "/api/v1/calendars/events", params={
            "start": start.strftime("%Y-%m-%dT%H:%M:%S"),
            "end": end.strftime("%Y-%m-%dT%H:%M:%S"),
        })
        notes = owui_call(token, "GET", "/api/v1/notes/")
    except Exception as exc:  # noqa: BLE001
        return HTMLResponse(page("Календарь и заметки", "", 
                                 f"Open WebUI недоступен: {type(exc).__name__}", True,
                                 wide=True), status_code=502)

    rows = []
    for event in sorted(events, key=lambda item: item.get("start_at", 0)):
        moment = datetime.fromtimestamp(event["start_at"] / 1e9)
        rows.append(
            f'<tr><td>{moment:%d.%m %H:%M}</td><td>{esc(event.get("title", ""))}</td>'
            f'<td class="dim">{esc(event.get("description") or "")}</td>'
            f'<td><form method="post" action="/portal/quick/event/delete">'
            f'<input type="hidden" name="csrf" value="{csrf_token()}">'
            f'<input type="hidden" name="id" value="{esc(event["id"])}">'
            f'<button class="link">удалить</button></form></td></tr>')
    event_table = ("<table><tr><th>Когда</th><th>Событие</th><th>Описание</th><th></th></tr>"
                   + "".join(rows) + "</table>") if rows else "<p class=dim>Событий нет.</p>"

    note_rows = []
    for note in notes[:50]:
        content = ((note.get("data") or {}).get("content") or {}).get("md", "")
        note_rows.append(
            f'<tr><td>{esc(note.get("title", "без названия"))}</td>'
            f'<td class="dim">{esc(content[:160])}</td>'
            f'<td><form method="post" action="/portal/quick/note/delete">'
            f'<input type="hidden" name="csrf" value="{csrf_token()}">'
            f'<input type="hidden" name="id" value="{esc(note["id"])}">'
            f'<button class="link">удалить</button></form></td></tr>')
    note_table = ("<table><tr><th>Заметка</th><th>Текст</th><th></th></tr>"
                  + "".join(note_rows) + "</table>") if note_rows else \
                 "<p class=dim>Заметок нет.</p>"

    body = f"""
<p class="sub">Вошли как <b>{esc(session.get("u", ""))}</b> · <a href="/portal/account">настройки</a></p>
<h2>Календарь</h2>
{event_table}
<form method="post" action="/portal/quick/event" class="row">
  <input type="hidden" name="csrf" value="{csrf_token()}">
  <input name="title" placeholder="Событие" required>
  <input type="date" name="date" value="{start:%Y-%m-%d}" required>
  <input type="time" name="time" value="10:00" required>
  <select name="minutes"><option value="30">30 мин</option>
    <option value="60" selected>1 час</option><option value="120">2 часа</option></select>
  <button type="submit">Добавить</button>
</form>
<h2>Заметки</h2>
{note_table}
<form method="post" action="/portal/quick/note">
  <input type="hidden" name="csrf" value="{csrf_token()}">
  <input name="title" placeholder="Заголовок" required>
  <textarea name="text" rows="4" placeholder="Текст заметки"></textarea>
  <button type="submit">Сохранить</button>
</form>"""
    extra_css = """
<style>
h2 { font-size:14px; text-transform:uppercase; letter-spacing:.08em;
     color:var(--yellow); margin:26px 0 10px; }
table { width:100%; border-collapse:collapse; font-size:14px; }
th,td { text-align:left; padding:8px 10px; border-bottom:1px solid var(--bg2); vertical-align:top; }
th { color:var(--gray); font-weight:400; font-size:12px; }
.dim { color:var(--gray); }
.row { display:flex; gap:8px; margin-top:12px; flex-wrap:wrap; }
.row input, .row select { flex:1; min-width:120px; }
button { padding:10px 16px; border-radius:8px; border:1px solid var(--bg2);
         background:var(--bg2); color:var(--fg); font:inherit; cursor:pointer; }
button:hover { border-color:var(--yellow); }
button.link { background:none; border:none; color:var(--gray); padding:0; }
textarea { width:100%; padding:10px 12px; border-radius:8px; border:1px solid var(--bg2);
           background:var(--bg); color:var(--fg); font:inherit; }
</style>"""
    return HTMLResponse(page("Календарь и заметки", extra_css + body, message, error, wide=True))


@app.get("/quick", response_class=HTMLResponse)
def quick(request: Request):
    session = read_session(request)
    if not session:
        return RedirectResponse("/portal/login", status_code=303)
    return quick_page(session)


@app.post("/quick/event", response_class=HTMLResponse)
def quick_event(request: Request, title: str = Form(""), date: str = Form(""),
                time: str = Form("10:00"), minutes: int = Form(60),
                csrf: str = Form("")):
    session = read_session(request)
    if not session:
        return RedirectResponse("/portal/login", status_code=303)
    if not csrf_ok(csrf):
        return quick_page(session, "Форма устарела, обновите страницу.", True)
    token = session.get("t", "")
    try:
        moment = datetime.strptime(f"{date} {time}", "%Y-%m-%d %H:%M")
        calendars = owui_call(token, "GET", "/api/v1/calendars/")
        calendar_id = calendars[0]["id"]
        owui_call(token, "POST", "/api/v1/calendars/events/create", body={
            "calendar_id": calendar_id, "title": title.strip(),
            "description": "", "location": "", "all_day": False,
            "start_at": int(moment.timestamp() * 1e9),
            "end_at": int((moment + timedelta(minutes=minutes)).timestamp() * 1e9),
        })
    except Exception as exc:  # noqa: BLE001
        return quick_page(session, f"Не удалось создать событие: {type(exc).__name__}", True)
    return RedirectResponse("/portal/quick", status_code=303)


@app.post("/quick/event/delete", response_class=HTMLResponse)
def quick_event_delete(request: Request, id: str = Form(""), csrf: str = Form("")):
    session = read_session(request)
    if not session:
        return RedirectResponse("/portal/login", status_code=303)
    if csrf_ok(csrf):
        try:
            owui_call(session.get("t", ""), "DELETE", f"/api/v1/calendars/events/{id}/delete")
        except Exception:  # noqa: BLE001
            pass
    return RedirectResponse("/portal/quick", status_code=303)


@app.post("/quick/note", response_class=HTMLResponse)
def quick_note(request: Request, title: str = Form(""), text: str = Form(""),
               csrf: str = Form("")):
    session = read_session(request)
    if not session:
        return RedirectResponse("/portal/login", status_code=303)
    if not csrf_ok(csrf):
        return quick_page(session, "Форма устарела, обновите страницу.", True)
    try:
        owui_call(session.get("t", ""), "POST", "/api/v1/notes/create", body={
            "title": title.strip(), "data": {"content": {"md": text}}})
    except Exception as exc:  # noqa: BLE001
        return quick_page(session, f"Не удалось сохранить заметку: {type(exc).__name__}", True)
    return RedirectResponse("/portal/quick", status_code=303)


@app.post("/quick/note/delete", response_class=HTMLResponse)
def quick_note_delete(request: Request, id: str = Form(""), csrf: str = Form("")):
    session = read_session(request)
    if not session:
        return RedirectResponse("/portal/login", status_code=303)
    if csrf_ok(csrf):
        try:
            owui_call(session.get("t", ""), "DELETE", f"/api/v1/notes/{id}/delete")
        except Exception:  # noqa: BLE001
            pass
    return RedirectResponse("/portal/quick", status_code=303)


@app.get("/admin/status", response_class=HTMLResponse)
def admin_status(request: Request):
    """Панель устройства: только владелец. Данные пишет cron раз в минуту."""
    session = read_session(request)
    if not session:
        return RedirectResponse("/portal/login", status_code=303)
    if not OWNER or session.get("u") != OWNER:
        return HTMLResponse(page("Доступ закрыт",
                                 '<p class="sub">Страница только для владельца.</p>'),
                            status_code=403)
    try:
        data = json.loads(STATUS_FILE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — нет файла или он ещё не создан
        data = {}
    return HTMLResponse(render_status(data))


def _fmt(value: object, unit: str = "") -> str:
    if value is None:
        return "—"
    return f"{value}{unit}"


def render_status(d: dict) -> str:
    temp = d.get("temp_c")
    temp_color = "#b8bb26"
    if isinstance(temp, (int, float)):
        temp_color = ("#fb4934" if temp >= 75 else
                      "#fabd2f" if temp >= 65 else "#b8bb26")
    mem = d.get("mem") or {}
    disks = d.get("disk") or {}
    cert = d.get("cert_days")
    cert_color = "#fb4934" if isinstance(cert, int) and cert < 14 else "#b8bb26"
    unhealthy = (d.get("containers") or {}).get("unhealthy") or []
    flags = d.get("throttle_flags") or []
    rows_disk = "".join(
        f"<tr><td>{esc(path)}</td><td>{_fmt(v.get('used_gb'))} ГБ / "
        f"{_fmt(v.get('total_gb'))} ГБ</td><td>{_fmt(v.get('pct'))}%</td></tr>"
        for path, v in disks.items())
    flags_html = (", ".join(esc(f) for f in flags) if flags
                  else "нет")
    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="10">
<title>Устройство — OmniSecretary</title>
<style>
:root {{ --bg:#282828; --bg1:#3c3836; --bg2:#504945; --fg:#ebdbb2;
        --gray:#928374; --yellow:#fabd2f; --green:#b8bb26; --red:#fb4934;
        --blue:#83a598; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--fg);
       font:15px/1.5 ui-monospace,"JetBrains Mono",monospace; padding:24px; }}
main {{ max-width:900px; margin:0 auto; }}
h1 {{ font-size:20px; margin:0 0 4px; }} h1 span {{ color:var(--yellow); }}
p.sub {{ color:var(--gray); font-size:13px; margin:0 0 20px; }}
.grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(260px,1fr));
        gap:14px; }}
.card {{ background:var(--bg1); border:1px solid var(--bg2);
        border-radius:12px; padding:16px 18px; }}
.card h2 {{ font-size:13px; text-transform:uppercase; letter-spacing:.08em;
           color:var(--gray); margin:0 0 10px; }}
.big {{ font-size:32px; font-weight:700; color:{temp_color}; }}
.kv {{ display:flex; justify-content:space-between; gap:12px; padding:3px 0; }}
.kv span:first-child {{ color:var(--gray); }}
table {{ width:100%; border-collapse:collapse; font-size:13px; }}
td {{ padding:3px 0; border-bottom:1px solid var(--bg2); }}
tr:last-child td {{ border-bottom:0; }}
a {{ color:var(--blue); }}
</style></head>
<body><main>
<h1>Устройство <span>OmniSecretary</span></h1>
<p class="sub">Обновление каждые 10 с · снимок {esc(d.get('time', '—'))} ·
модель {esc(d.get('model', '—'))}</p>
<div class="grid">
  <div class="card"><h2>Температура</h2>
    <div class="big">{_fmt(temp)} °C</div>
    <div class="kv"><span>частота</span><span>{_fmt(d.get('freq_mhz'))} МГц</span></div>
    <div class="kv"><span>троттлинг</span><span>{esc(d.get('throttled', '—'))}</span></div>
    <div class="kv"><span>флаги</span><span>{flags_html}</span></div>
  </div>
  <div class="card"><h2>Процессор</h2>
    <div class="big">{esc(d.get('load1', '—'))}</div>
    <div class="kv"><span>load 1/5/15</span>
      <span>{esc(d.get('load', '—'))}</span></div>
    <div class="kv"><span>ядер</span><span>{_fmt(d.get('cores'))}</span></div>
    <div class="kv"><span>uptime</span><span>{esc(d.get('uptime', '—'))}</span></div>
  </div>
  <div class="card"><h2>Память</h2>
    <div class="big">{_fmt(mem.get('used_mb'))} МБ</div>
    <div class="kv"><span>всего</span><span>{_fmt(mem.get('total_mb'))} МБ</span></div>
    <div class="kv"><span>доступно</span><span>{_fmt(mem.get('available_mb'))} МБ</span></div>
    <div class="kv"><span>swap</span><span>{_fmt(mem.get('swap_used_mb'))} /
      {_fmt(mem.get('swap_total_mb'))} МБ</span></div>
  </div>
  <div class="card"><h2>Сервисы</h2>
    <div class="big">{_fmt((d.get('containers') or {}).get('running'))} /
      {_fmt((d.get('containers') or {}).get('total'))}</div>
    <div class="kv"><span>нездоровые</span>
      <span>{esc(', '.join(unhealthy)) if unhealthy else 'нет'}</span></div>
    <div class="kv"><span>ядро</span><span>{esc(d.get('kernel', '—'))}</span></div>
  </div>
  <div class="card"><h2>Диски</h2>
    <table>{rows_disk or '<tr><td>нет данных</td></tr>'}</table>
  </div>
  <div class="card"><h2>Обслуживание</h2>
    <div class="kv"><span>сертификат</span>
      <span style="color:{cert_color}">{_fmt(cert)} дн.</span></div>
    <div class="kv"><span>последний бэкап</span>
      <span>{esc((d.get('backup') or {}).get('last', '—'))}</span></div>
    <div class="kv"><span>размер бэкапа</span>
      <span>{_fmt((d.get('backup') or {}).get('size_mb'))} МБ</span></div>
    <div class="kv"><span>firewall</span><span>{esc(d.get('ufw', '—'))}</span></div>
  </div>
</div>
<p class="sub" style="margin-top:20px">
  <a href="/portal/account">Настройки</a> · <a href="/">В чат</a></p>
</main></body></html>"""


@app.get("/logout")
def logout():
    response = RedirectResponse("/portal/login", status_code=303)
    response.delete_cookie("portal_session")
    return response


@app.get("/account", response_class=HTMLResponse)
def account(request: Request, message: str = "", error: int = 0):
    session = read_session(request)
    if not session:
        return RedirectResponse("/portal/login", status_code=303)
    conn = db()
    profile = conn.execute("SELECT * FROM profiles WHERE owui_id = ?",
                           (session["uid"],)).fetchone()
    conn.close()
    if not profile:
        return RedirectResponse("/portal/login", status_code=303)

    has_token = "задан" if profile["github_token"] else "не задан"
    preset_options = "".join(
        f'<option value="{name}"{" selected" if name == profile["preset"] else ""}>{name}</option>'
        for name in PRESETS)
    admin_link = ('<a href="/portal/admin/status">Устройство</a> · '
                  if OWNER and session.get("u") == OWNER else "")
    body = f"""
<form method="post" action="/portal/account">
  <input type="hidden" name="csrf" value="{csrf_token()}">
  <label>Логин</label>
  <input value="{esc(profile['username'])}" disabled>
  <label>Город для погоды</label>
  <input name="city" value="{esc(profile['city'])}"
         placeholder="Санкт-Петербург, Ломоносовская">
  <label>Telegram ID (для брифинга и настроек бота)</label>
  <input name="telegram_id" inputmode="numeric"
         value="{esc(profile['telegram_id'] or '')}" placeholder="123456789">
  <label>GitHub-токен (PAT, только для ваших задач; сейчас {has_token})</label>
  <input name="github_token" type="password" autocomplete="off"
         placeholder="оставьте пустым, чтобы не менять">
  <label>Пресет по умолчанию</label>
  <select name="preset">{preset_options}</select>
  <button type="submit">Сохранить</button>
</form>
<p class="foot">{admin_link}<a href="/">В чат</a> · <a href="/portal/logout">Выйти</a></p>"""
    return HTMLResponse(page("Личные настройки", body, message, bool(error)))


@app.post("/account", response_class=HTMLResponse)
def account_save(request: Request, city: str = Form(""), telegram_id: str = Form(""),
                 github_token: str = Form(""), preset: str = Form("secretary"),
                 csrf: str = Form("")):
    session = read_session(request)
    if not session:
        return RedirectResponse("/portal/login", status_code=303)
    if not csrf_ok(csrf):
        return RedirectResponse("/portal/account?error=1&message=Форма+устарела",
                                status_code=303)

    preset = preset if preset in PRESETS else "secretary"
    tg_id: int | None = None
    if telegram_id.strip():
        if not telegram_id.strip().isdigit():
            return RedirectResponse(
                "/portal/account?error=1&message=Telegram+ID+должен+быть+числом",
                status_code=303)
        tg_id = int(telegram_id.strip())

    conn = db()
    profile = conn.execute("SELECT * FROM profiles WHERE owui_id = ?",
                           (session["uid"],)).fetchone()
    if not profile:
        conn.close()
        return RedirectResponse("/portal/login", status_code=303)
    token_value = github_token.strip()
    if token_value:
        token_value = enc_token(token_value)
    else:
        token_value = profile["github_token"]
    conn.execute(
        "UPDATE profiles SET city = ?, telegram_id = ?, github_token = ?, "
        "preset = ? WHERE owui_id = ?",
        (city.strip(), tg_id, token_value, preset, session["uid"]))
    conn.commit()
    conn.close()

    # Город синхронизируем в базу бота: /weather и брифинг увидят настройку.
    if tg_id and city.strip():
        sync_city_to_bot(tg_id, city.strip())

    return RedirectResponse("/portal/account?message=Сохранено", status_code=303)


def sync_city_to_bot(tg_id: int, city: str) -> None:
    if not BOT_DB_PATH.exists():
        return
    lat, lon = geocode(city)
    if lat is None:
        return
    try:
        conn = sqlite3.connect(BOT_DB_PATH, timeout=10)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS settings (tg_id INTEGER PRIMARY KEY, "
            "city TEXT NOT NULL, lat REAL NOT NULL, lon REAL NOT NULL)")
        conn.execute(
            "INSERT INTO settings (tg_id, city, lat, lon) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(tg_id) DO UPDATE SET city = excluded.city, "
            "lat = excluded.lat, lon = excluded.lon",
            (tg_id, city, lat, lon))
        conn.commit()
        conn.close()
    except sqlite3.Error:
        pass


def geocode(query: str) -> tuple[float | None, float | None]:
    params = urllib.parse.urlencode(
        {"q": query, "format": "jsonv2", "limit": 1, "accept-language": "ru"})
    try:
        with httpx.Client(timeout=20) as client:
            response = client.get(
                f"https://nominatim.openstreetmap.org/search?{params}",
                headers={"User-Agent": WEATHER_UA})
            found = response.json()
    except Exception:  # noqa: BLE001
        return None, None
    if not found:
        return None, None
    return float(found[0]["lat"]), float(found[0]["lon"])


# Шифруем токены, сохранённые открытым текстом до этой версии. Идемпотентно.
try:
    migrate_tokens()
except Exception:  # noqa: BLE001 — портал должен подняться даже при сбое миграции
    pass


# ── Мобильный API ────────────────────────────────────────────────────────
# Отдельный модуль: клиент приложения ходит с Bearer-токеном Open WebUI.
from mobile_api import router as mobile_router  # noqa: E402

app.include_router(mobile_router)
