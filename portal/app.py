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
import providers as llm
import vaults as pvaults
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
        --gray:#928374; --yellow:#fabd2f; --orange:#fe8019; --green:#b8bb26;
        --red:#fb4934; --blue:#83a598; }}
* {{ box-sizing:border-box; }}
html,body {{ min-height:100vh; }}
body {{ margin:0; min-height:100vh; color:var(--fg); font:15px/1.6 system-ui,-apple-system,"Segoe UI",sans-serif;
  background:
    radial-gradient(900px 500px at 12% 12%, rgba(250,189,47,.13), transparent 60%),
    radial-gradient(700px 500px at 88% 88%, rgba(254,128,25,.10), transparent 60%),
    linear-gradient(145deg,#1d2021 0%,#282828 45%,#32302f 100%);
  display:flex; align-items:center; justify-content:center; padding:32px 20px; }}
.shell {{ width:100%; max-width:520px; display:flex; flex-direction:column; align-items:center; gap:18px; }}
.brand {{ text-align:center; padding:0 8px 6px; }}
.mark {{ width:72px; height:72px; margin:0 auto 14px; filter:drop-shadow(0 8px 18px rgba(250,189,47,.18)); }}
.brand h2 {{ font-size:30px; line-height:1.1; margin:0 0 8px; letter-spacing:-.5px; }}
.brand h2 b {{ color:var(--yellow); font-weight:800; }}
.brand p {{ color:#bdae93; margin:0 auto 10px; max-width:440px; font-size:14px; }}
.chip {{ display:inline-flex; gap:8px; align-items:center; padding:7px 12px; border-radius:999px;
  background:rgba(80,73,69,.5); border:1px solid #504945; color:var(--gray); font-size:12px; }}
.card {{ width:100%; background:linear-gradient(180deg,rgba(60,56,54,.96),rgba(40,40,40,.96));
  border:1px solid #504945; border-radius:20px; padding:32px;
  box-shadow:0 24px 60px rgba(0,0,0,.35), inset 0 1px 0 rgba(255,255,255,.03); }}
.card-head {{ display:flex; align-items:center; gap:10px; margin-bottom:22px;
  color:var(--gray); font-size:12px; text-transform:uppercase; letter-spacing:1.4px; }}
.card-head::before {{ content:""; width:9px; height:9px; border-radius:50%;
  background:var(--yellow); box-shadow:0 0 12px var(--yellow); }}
h1 {{ font-size:24px; line-height:1.25; margin:0 0 6px; }}
p.sub {{ color:var(--gray); margin:0 0 22px; font-size:13px; }}
label {{ display:block; font-size:12px; color:#bdae93; margin:16px 0 7px;
  text-transform:uppercase; letter-spacing:.6px; }}
input,select {{ width:100%; padding:12px 14px; border-radius:10px;
  border:1px solid #504945; background:#1d2021; color:var(--fg);
  font:14px/1.4 ui-monospace,"JetBrains Mono",monospace; transition:.18s; }}
input:focus,select:focus {{ outline:none; border-color:var(--yellow);
  box-shadow:0 0 0 3px rgba(250,189,47,.14); }}
button {{ margin-top:22px; width:100%; padding:13px 16px; border:0; border-radius:10px;
  background:linear-gradient(135deg,var(--yellow),var(--orange));
  color:#282828; font:700 14px/1 system-ui,sans-serif; cursor:pointer;
  box-shadow:0 8px 22px rgba(250,189,47,.18); transition:.18s; }}
button:hover {{ filter:brightness(1.08); transform:translateY(-1px); }}
.notice {{ margin:0 0 18px; padding:12px 14px; border-left:3px solid var(--green);
  background:rgba(40,40,40,.7); font-size:13px; border-radius:0 8px 8px 0; }}
a {{ color:var(--yellow); text-decoration:none; }}
a:hover {{ text-decoration:underline; }}
.btn {{ display:block; margin-top:14px; width:100%; padding:12px 16px; border-radius:10px;
  background:rgba(80,73,69,.45); border:1px solid #504945; color:var(--fg);
  text-align:center; text-decoration:none; font:700 14px/1 system-ui,sans-serif; }}
.btn:hover {{ background:#504945; text-decoration:none; }}
.pwd {{ position:relative; }}
.pwd input {{ padding-right:96px; }}
.eye {{ position:absolute; right:7px; top:50%; transform:translateY(-50%);
  width:auto; margin:0; padding:7px 10px; background:transparent; border:1px solid #504945;
  border-radius:7px; color:var(--yellow); font-size:12px; box-shadow:none; }}
.eye:hover {{ transform:translateY(-50%); background:rgba(250,189,47,.08); }}
.foot {{ margin-top:18px; font-size:12px; color:var(--gray); text-align:center; }}
.wide .shell {{ max-width:980px; }}
.wide .card {{ max-width:none; }}
@media (max-width:820px) {{
  body {{ padding:20px 14px; }}
  .shell {{ gap:14px; }}
  .card {{ padding:24px 20px; }}
}}
</style></head><body{" class=\"wide\"" if wide else ""}>
<div class="shell">
  <section class="brand">
    <img class="mark" src="/omni-logo.svg" alt="OmniSecretary" width="64" height="64">
    <h2><b>Omni</b>Secretary</h2>
    <p>Ваш собственный ассистент: чат, календарь, задачи, заметки и голос — в одном месте.</p>
    <span class="chip">self-hosted · приватный</span>
  </section>
  <section class="card">
    <div class="card-head">Личный кабинет</div>
    <h1>{esc(title)}</h1>
    <p class="sub">Личное пространство OmniSecretary</p>{notice}{body}
  </section>
</div>
<script>
function togglePw(el) {{
  const input = el.parentElement.querySelector('input');
  if (!input) return;
  const show = input.type === 'password';
  input.type = show ? 'text' : 'password';
  el.textContent = show ? 'Скрыть' : 'Показать';
}}
</script>
</body></html>"""


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
  <div class="pwd">
    <input type="password" name="password" required minlength="8"
           autocomplete="new-password">
    <button type="button" class="eye" onclick="togglePw(this)">Показать</button>
  </div>
  <label>Пароль ещё раз</label>
  <div class="pwd">
    <input type="password" name="password2" required minlength="8"
           autocomplete="new-password">
    <button type="button" class="eye" onclick="togglePw(this)">Показать</button>
  </div>
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

    # Сразу пускаем пользователя в личные настройки: там он добавит
    # GitHub-токен, город для погоды и пресет, как и задумывалось.
    created_token, created_id, login_error = owui_login(username, password)
    if login_error or not created_token:
        body = f"""
<p class="sub">Аккаунт создан, но автоматический вход не удался: {esc(login_error)}</p>
<p><a class="btn" href="/portal/login">Войти под своим логином</a></p>"""
        return HTMLResponse(page("Готово", body))
    message = "Аккаунт создан. Заполните личные настройки."
    body = f"""
<p class="sub">Аккаунт создан. Сейчас откроются настройки, где можно добавить
GitHub-токен, город для погоды, Telegram ID и пресет по умолчанию.</p>
<script>
localStorage.setItem("token", {json.dumps(created_token)});
location.replace("/portal/account?message=" + {json.dumps(urllib.parse.quote(message))});
</script>
<noscript><a class="btn" href="/portal/account">Открыть настройки</a></noscript>
<p class="foot">Тот же логин и пароль работают в чате Open WebUI.</p>"""
    response = HTMLResponse(page("Готово", body))
    response.set_cookie("portal_session",
                        make_session(created_id, username, created_token),
                        max_age=SESSION_TTL, httponly=True, samesite="lax")
    return response


@app.get("/login", response_class=HTMLResponse)
def login_form(message: str = "", error: int = 0):
    body = """
<form method="post" action="/portal/login">
  <label>Логин</label>
  <input name="username" required autocomplete="username" autofocus>
  <label>Пароль</label>
  <div class="pwd">
    <input type="password" name="password" required autocomplete="current-password">
    <button type="button" class="eye" onclick="togglePw(this)">Показать</button>
  </div>
  <button type="submit">Войти</button>
</form>
<p class="foot">Нет аккаунта?</p>
<a class="btn" href="/portal/register">Зарегистрироваться</a>"""
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
    return RedirectResponse("/portal/workspace", status_code=303)


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
    admin_links = ""
    if OWNER and session.get("u") == OWNER:
        admin_links = ('<a href="/portal/admin/status">Устройство</a> · '
                       '<a href="/portal/admin/llm">Провайдер (система)</a> · ')
    body = f"""
<p class="sub">Данные вашего аккаунта: город для погоды, Telegram ID,
GitHub-токен и пресет по умолчанию. Токен хранится зашифрованно.</p>
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
<div class="notice" style="margin-top:22px">
  <b>Нейросеть для чата.</b> Можно подключить свой OpenAI-совместимый провайдер
  и ключ — он будет использоваться только в вашем аккаунте.
  <a href="/portal/llm">Настроить личный провайдер →</a>
</div>
<p class="foot">{admin_links}<a href="/">В чат</a> · <a href="/portal/logout">Выйти</a></p>"""
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


# ── Провайдеры LLM ───────────────────────────────────────────────────────

def _provider_form(action: str, info: dict, title: str, intro: str,
                   back: str = "/portal/account") -> str:
    enabled = "checked" if info.get("enabled") else ""
    fallback = "checked" if info.get("fallback", True) else ""
    key_hint = "сохранён (оставьте пустым, чтобы не менять)" if info.get("has_key") else "не задан"
    return f"""
<p class="sub">{esc(intro)}</p>
<form method="post" action="{esc(action)}">
  <input type="hidden" name="csrf" value="{csrf_token()}">
  <label style="text-transform:none;letter-spacing:0;display:flex;gap:10px;align-items:center">
    <input type="checkbox" name="enabled" value="1" {enabled} style="width:auto;margin:0">
    Включить этого провайдера
  </label>
  <label>Название (для себя)</label>
  <input name="name" value="{esc(info.get('name',''))}" placeholder="Например, OpenRouter / мой прокси">
  <label>Базовый URL (OpenAI-совместимый)</label>
  <input name="base_url" value="{esc(info.get('base_url',''))}"
         placeholder="https://openrouter.ai/api/v1">
  <label>API-ключ ({key_hint})</label>
  <div class="pwd"><input id="llmkey" name="api_key" type="password" autocomplete="off"
         placeholder="sk-... или оставьте пустым"><button type="button" class="eye"
         onclick="togglePw(this)">Показать</button></div>
  <label>Модель по умолчанию</label>
  <input name="model" value="{esc(info.get('model',''))}"
         placeholder="openai/gpt-4o-mini">
  <label style="text-transform:none;letter-spacing:0;display:flex;gap:10px;align-items:center">
    <input type="checkbox" name="fallback" value="1" {fallback} style="width:auto;margin:0">
    При ошибке/лимите переключаться на бесплатную модель
  </label>
  <button type="submit">Сохранить</button>
</form>
<form method="post" action="{esc(action)}/clear" style="margin-top:10px">
  <input type="hidden" name="csrf" value="{csrf_token()}">
  <button type="submit" class="btn" style="margin-top:0;background:rgba(251,73,52,.12);border-color:#fb4934;color:#fb4934">Удалить настройки провайдера</button>
</form>
<p class="foot"><a href="{esc(back)}">← Назад</a> · <a href="/">В чат</a></p>"""


@app.get("/llm", response_class=HTMLResponse)
def llm_personal(request: Request, message: str = "", error: int = 0):
    session = read_session(request)
    if not session:
        return RedirectResponse("/portal/login", status_code=303)
    info = llm.public(session["uid"])
    body = _provider_form("/portal/llm", info, "Мой провайдер нейросети",
        "Ваш личный OpenAI-совместимый провайдер и ключ. Используется только в "
        "вашем аккаунте (в веб-чате и приложении), ключ хранится зашифрованно на "
        "сервере и не показывается в браузере. Если не включён — используется "
        "общесистемный провайдер или бесплатная модель.")
    return HTMLResponse(page("Мой провайдер", body, message, bool(error)))


@app.post("/llm", response_class=HTMLResponse)
def llm_personal_save(request: Request, name: str = Form(""), base_url: str = Form(""),
                      api_key: str = Form(""), model: str = Form(""),
                      enabled: str = Form(""), fallback: str = Form(""),
                      csrf: str = Form("")):
    session = read_session(request)
    if not session:
        return RedirectResponse("/portal/login", status_code=303)
    if not csrf_ok(csrf):
        return RedirectResponse("/portal/llm?error=1&message=Форма+устарела", status_code=303)
    if enabled and not base_url.strip():
        return RedirectResponse("/portal/llm?error=1&message=Укажите+базовый+URL", status_code=303)
    llm.save(session["uid"], name=name, base_url=base_url, api_key=api_key, model=model,
             enabled=bool(enabled), fallback=bool(fallback),
             updated_at=datetime.now(timezone.utc).isoformat())
    return RedirectResponse("/portal/llm?message=Сохранено", status_code=303)


@app.post("/llm/clear", response_class=HTMLResponse)
def llm_personal_clear(request: Request, csrf: str = Form("")):
    session = read_session(request)
    if not session:
        return RedirectResponse("/portal/login", status_code=303)
    if not csrf_ok(csrf):
        return RedirectResponse("/portal/llm?error=1&message=Форма+устарела", status_code=303)
    llm.delete(session["uid"])
    return RedirectResponse("/portal/llm?message=Личный+провайдер+удалён", status_code=303)


@app.get("/admin/llm", response_class=HTMLResponse)
def llm_admin(request: Request, message: str = "", error: int = 0):
    session = read_session(request)
    if not session or not OWNER or session.get("u") != OWNER:
        return RedirectResponse("/portal/login", status_code=303)
    info = llm.public(llm.SYSTEM_KEY)
    body = _provider_form("/portal/admin/llm", info, "Общесистемный провайдер",
        "Провайдер и ключ для всех пользователей по умолчанию (если у пользователя "
        "нет личного). Ключ хранится зашифрованно на сервере. OpenRouter из .env "
        "остаётся запасным вариантом, если системный провайдер не задан.")
    body += '<p class="foot"><a href="/portal/admin/status">Устройство</a> · <a href="/portal/account">Настройки</a></p>'
    return HTMLResponse(page("Провайдер (система)", body, message, bool(error)))


@app.post("/admin/llm", response_class=HTMLResponse)
def llm_admin_save(request: Request, name: str = Form(""), base_url: str = Form(""),
                   api_key: str = Form(""), model: str = Form(""),
                   enabled: str = Form(""), fallback: str = Form(""),
                   csrf: str = Form("")):
    session = read_session(request)
    if not session or not OWNER or session.get("u") != OWNER:
        return RedirectResponse("/portal/login", status_code=303)
    if not csrf_ok(csrf):
        return RedirectResponse("/portal/admin/llm?error=1&message=Форма+устарела", status_code=303)
    if enabled and not base_url.strip():
        return RedirectResponse("/portal/admin/llm?error=1&message=Укажите+базовый+URL", status_code=303)
    llm.save(llm.SYSTEM_KEY, name=name, base_url=base_url, api_key=api_key, model=model,
             enabled=bool(enabled), fallback=bool(fallback),
             updated_at=datetime.now(timezone.utc).isoformat())
    return RedirectResponse("/portal/admin/llm?message=Сохранено", status_code=303)


@app.post("/admin/llm/clear", response_class=HTMLResponse)
def llm_admin_clear(request: Request, csrf: str = Form("")):
    session = read_session(request)
    if not session or not OWNER or session.get("u") != OWNER:
        return RedirectResponse("/portal/login", status_code=303)
    if not csrf_ok(csrf):
        return RedirectResponse("/portal/admin/llm?error=1&message=Форма+устарела", status_code=303)
    llm.delete(llm.SYSTEM_KEY)
    return RedirectResponse("/portal/admin/llm?message=Системный+провайдер+удалён", status_code=303)



# ── Панель администратора: модели, токены, ключи ────────────────────────

def _admin_request(method: str, path: str, *, body: dict | None = None,
                   params: dict | None = None) -> object:
    """Запрос к Open WebUI от имени администратора."""
    with httpx.Client(timeout=25) as client:
        response = client.request(
            method, f"{OWUI_URL}{path}", json=body, params=params,
            headers={"Authorization": f"Bearer {admin_token()}"})
    if response.status_code not in (200, 201):
        raise RuntimeError(f"Open WebUI: {path} → HTTP {response.status_code}")
    return response.json() if response.content else {}


def _owner_or_403(request: Request):
    session = read_session(request)
    if not session:
        return None, RedirectResponse("/portal/login", status_code=303)
    if not OWNER or session.get("u") != OWNER:
        return None, HTMLResponse(
            page("Доступ закрыт", '<p class="sub">Страница только для владельца.</p>'),
            status_code=403)
    return session, None


def _key_status(name: str) -> str:
    value = os.environ.get(name, "").strip()
    return "задан" if value else "не задан"


@app.get("/admin/control", response_class=HTMLResponse)
def admin_control(request: Request, message: str = "", error: int = 0):
    """Модели, расход токенов и состояние ключей — только владелец."""
    session, denied = _owner_or_403(request)
    if denied:
        return denied
    try:
        models_data = _admin_request(
            "GET", "/api/v1/models/list", params={"page": 1})
        models = models_data.get("items", []) if isinstance(models_data, dict) else []
        usage = _admin_request("GET", "/api/v1/users/usage")
    except Exception as exc:  # noqa: BLE001 — панель должна открываться и при сбое API
        models, usage = [], {}
        message = message or str(exc)

    cards = []
    for model in models[:40]:
        mid = str(model.get("id", ""))
        detail = {}
        try:
            detail = _admin_request(
                "GET", "/api/v1/models/model", params={"id": mid})
        except Exception:  # noqa: BLE001 — битая модель не ломает всю страницу
            pass
        params = detail.get("params") or model.get("params") or {}
        meta = detail.get("meta") or model.get("meta") or {}
        system = str(params.get("system") or "")
        tools = meta.get("toolIds") or []
        if isinstance(tools, dict):
            tools = list(tools.keys())
        active = bool(detail.get("is_active", model.get("is_active", True)))
        base = str(detail.get("base_model_id") or model.get("base_model_id") or "")
        cards.append(f"""
<div class="mcard">
  <div class="mhead"><b>{esc(mid)}</b> <span class="pill">{'вкл' if active else 'выкл'}</span></div>
  <div class="muted">{esc(model.get('name') or mid)} · база: {esc(base or '—')}</div>
  <form method="post" action="/portal/admin/control/model">
    <input type="hidden" name="csrf" value="{csrf_token()}">
    <input type="hidden" name="id" value="{esc(mid)}">
    <label>Системный промпт (params.system)</label>
    <textarea name="system" rows="5">{esc(system)}</textarea>
    <label>Инструменты (toolIds, через запятую)</label>
    <input name="tools" value="{esc(', '.join(str(t) for t in tools))}"
           placeholder="web_search, mcp-vault">
    <label class="check"><input type="checkbox" name="active" value="1"
      {'checked' if active else ''}> доступна в списке моделей</label>
    <button type="submit">Сохранить модель</button>
  </form>
</div>""")

    totals = usage.get("totals") or {}
    top_models = usage.get("top_models") or []
    usage_rows = "".join(
        f"<tr><td>{esc(row.get('model_id', '—'))}</td>"
        f"<td>{esc(row.get('messages', 0))}</td>"
        f"<td>{esc(row.get('input_tokens', 0))}</td>"
        f"<td>{esc(row.get('output_tokens', 0))}</td>"
        f"<td><b>{esc(row.get('total_tokens', 0))}</b></td></tr>"
        for row in top_models[:12])
    keys = [
        ("OPENROUTER_KEY", "OpenRouter"),
        ("OPENAI_API_KEY", "OpenAI / Basic LLM"),
        ("LLM_USER_JWT_SECRET", "Per-user JWT шлюза"),
        ("WEBUI_API_KEY", "API Open WebUI"),
        ("VIKUNJA_API_TOKEN", "Vikunja"),
        ("GITHUB_TOKEN", "GitHub MCP"),
        ("TELEGRAM_BOT_TOKEN", "Telegram (профиль выключен)"),
        ("PORTAL_SECRET", "Сессия портала"),
    ]
    def key_row(name: str, label: str) -> str:
        status = _key_status(name)
        css = "ok" if status == "задан" else "bad"
        return (f"<tr><td>{esc(label)}</td><td>{esc(name)}</td>"
                f'<td class="{css}">{status}</td></tr>')

    key_rows = "".join(key_row(name, label) for name, label in keys)
    system_cfg = llm.public(llm.SYSTEM_KEY)
    body = f"""
<style>
.ac {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(320px,1fr)); gap:16px; }}
.ac .mcard, .ac .panel {{ background:var(--bg1); border:1px solid var(--bg2);
  border-radius:12px; padding:16px; }}
.ac .mhead {{ display:flex; justify-content:space-between; gap:8px; }}
.ac .pill {{ font-size:11px; padding:2px 8px; border-radius:99px; background:var(--bg2); }}
.ac .muted, .ac .muted a {{ color:var(--gray); font-size:12px; }}
.ac label {{ margin:12px 0 5px; }}
.ac textarea {{ width:100%; padding:10px; border-radius:8px; border:1px solid var(--bg2);
  background:var(--bg0); color:var(--fg); font:12px/1.5 ui-monospace,monospace; }}
.ac .check {{ display:flex; align-items:center; gap:8px; }}
.ac .check input {{ width:auto; }}
.ac table {{ width:100%; border-collapse:collapse; font-size:12px; }}
.ac td, .ac th {{ padding:5px 6px; border-bottom:1px solid var(--bg2); text-align:left; }}
.ac .ok {{ color:var(--green); }} .ac .bad {{ color:var(--red); }}
</style>
<div class="ac">
  <div class="panel" style="grid-column:1/-1">
    <h2>Расход токенов и активность</h2>
    <p class="muted">Всего {esc(totals.get('lifetime_tokens', 0))} токенов ·
      ввод {esc(totals.get('input_tokens', 0))} · ответ {esc(totals.get('output_tokens', 0))} ·
      чатов {esc(totals.get('total_chats', 0))} · сообщений {esc(totals.get('messages', 0))}</p>
    <table><tr><th>Модель</th><th>Сообщ.</th><th>Ввод</th><th>Ответ</th><th>Всего</th></tr>
      {usage_rows or '<tr><td colspan="5">нет данных</td></tr>'}</table>
  </div>
  <div class="panel" style="grid-column:1/-1">
    <h2>Ключи</h2>
    <p class="muted">Значения не показываются — только факт наличия.
      Системный провайдер: {'вкл' if system_cfg.get('enabled') else 'выкл'} —
      <a href="/portal/llm">настроить</a>.</p>
    <table><tr><th>Назначение</th><th>Переменная</th><th>Статус</th></tr>{key_rows}</table>
  </div>
  {''.join(cards) or '<div class="panel">Модели не найдены.</div>'}
</div>"""
    return HTMLResponse(page("Управление", body, message, bool(error), wide=True))


@app.post("/admin/control/model")
def admin_control_model_save(
    request: Request,
    id: str = Form(...),
    system: str = Form(""),
    tools: str = Form(""),
    active: str = Form(""),
    csrf: str = Form(""),
):
    session, denied = _owner_or_403(request)
    if denied:
        return denied
    if not csrf_ok(csrf):
        return RedirectResponse(
            "/portal/admin/control?error=1&message=Форма+устарела", status_code=303)
    try:
        detail = _admin_request("GET", "/api/v1/models/model", params={"id": id})
        params = dict(detail.get("params") or {})
        meta = dict(detail.get("meta") or {})
        prompt = system.strip()
        if prompt:
            params["system"] = prompt
        else:
            params.pop("system", None)
        ids = [item.strip() for item in tools.split(",") if item.strip()]
        if ids:
            meta["toolIds"] = ids
        else:
            meta.pop("toolIds", None)
        _admin_request("POST", "/api/v1/models/model/update", body={
            "id": id,
            "name": detail.get("name") or id,
            "base_model_id": detail.get("base_model_id"),
            "meta": meta,
            "params": params,
            "is_active": active == "1",
            "commit_message": "Изменено через панель администратора",
        })
    except Exception as exc:  # noqa: BLE001
        return RedirectResponse(
            "/portal/admin/control?error=1&message="
            + urllib.parse.quote(str(exc)[:200]), status_code=303)
    return RedirectResponse("/portal/admin/control?message=Модель+обновлена", status_code=303)


# ── Поиск в интернете ────────────────────────────────────────────────────

WEBUI_KEY_FILE = Path(os.environ.get(
    "WEBUI_API_KEY_FILE", "/secrets/webui-api-key.txt"))

SEARCH_FIELDS = {
    "WEB_SEARCH_ENGINE": "Движок",
    "DDGS_BACKEND": "Бэкенд DuckDuckGo (auto/lite/html)",
    "WEB_SEARCH_RESULT_COUNT": "Число результатов",
    "WEB_SEARCH_CONCURRENT_REQUESTS": "Параллельные запросы",
    "WEB_SEARCH_DOMAIN_FILTER_LIST": "Фильтр доменов (JSON)",
    "BYPASS_WEB_SEARCH_WEB_LOADER": "Только сниппеты",
    "WEB_FETCH_MAX_CONTENT_LENGTH": "Лимит текста страницы",
    "TAVILY_SEARCH_DEPTH": "Глубина Tavily",
    "PERPLEXITY_SEARCH_CONTEXT_USAGE": "Контекст Perplexity",
}


def _owui_call(path: str, body: dict | None = None) -> dict:
    key = WEBUI_KEY_FILE.read_text().split("=", 1)[-1].strip()
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"http://open-webui:8080{path}", data=data,
        method="POST" if data else "GET",
        headers={"Authorization": f"Bearer {key}",
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as response:
        return json.load(response)


def _search_config() -> dict:
    current = _owui_call("/api/v1/retrieval/config")
    # Open WebUI отдаёт web-настройки вложенным объектом.
    web = current.get("web", current)
    return {k: web.get(k, "") for k in SEARCH_FIELDS}


@app.get("/search", response_class=HTMLResponse)
def search_page(request: Request, message: str = ""):
    session = read_session(request)
    if not session:
        return RedirectResponse("/portal/login", status_code=303)
    cfg = _search_config()
    options = "".join(
        f'<option value="{v}"{" selected" if str(cfg["WEB_SEARCH_ENGINE"]) == v else ""}>{label}</option>'
        for v, label in [
            ("duckduckgo", "DuckDuckGo (лёгкий)"),
            ("brave", "Brave"), ("tavily", "Tavily"),
            ("searxng", "SearXNG"), ("mojeek", "Mojeek"),
            ("bing", "Bing"), ("youcom", "You.com"),
            ("perplexity", "Perplexity"),
        ])
    rows = "".join(
        f'<label>{esc(label)}<input name="{k}" value="{esc(str(cfg.get(k, "")))}"></label>'
        for k, label in SEARCH_FIELDS.items() if k != "WEB_SEARCH_ENGINE")
    notice = f'<p class="ok">{esc(message)}</p>' if message else ""
    body = f"""
<h1>Поиск в интернете</h1>
{notice}
<p class="sub">Настройки общие для всех моделей Open WebUI. Для чистых источников используйте
  <b>DuckDuckGo</b> с бэкендом <code>lite</code> или <b>Mojeek</b>. Слишком большой лимит текста
  забивает контекст и замедляет ответ.</p>
<form method="post" action="/portal/search">
  <input type="hidden" name="csrf" value="{csrf_token()}">
  <label>Движок<select name="WEB_SEARCH_ENGINE">{options}</select></label>
  {rows}
  <div class="actions">
    <button class="btn primary" type="submit">Сохранить</button>
    <button class="btn" type="submit" name="test" value="1">Проверить поиск</button>
  </div>
</form>
<p class="sub">После сохранения обновите страницу чата (Ctrl+Shift+R).</p>
"""
    return ws_page("Поиск", body, session, "search")


@app.post("/search")
async def search_save(request: Request):
    session = read_session(request)
    if not session:
        return RedirectResponse("/portal/login", status_code=303)
    form = await request.form()
    if form.get("csrf") != csrf_token():
        return RedirectResponse("/portal/search?error=1", status_code=303)
    update = {}
    for key in SEARCH_FIELDS:
        value = str(form.get(key, "")).strip()
        if key in ("WEB_SEARCH_RESULT_COUNT", "WEB_SEARCH_CONCURRENT_REQUESTS",
                   "WEB_FETCH_MAX_CONTENT_LENGTH"):
            value = int(value or 0)
        elif key.endswith("LIST"):
            value = json.loads(value or "[]")
        elif key.startswith("BYPASS"):
            value = value.lower() in ("1", "true", "on", "да")
        update[key] = value
    # Open WebUI принимает web-настройки только вложенным объектом web.
    _owui_call("/api/v1/retrieval/config/update", {"web": update})
    # Запрос «тест поиска» в DDG возвращает мусор — используем стабильный эталон.
    if form.get("test"):
        test = _owui_call("/api/v1/retrieval/process/web/search",
                          {"queries": ["Raspberry Pi 5 official specifications"]})
        count = len(test.get("items", [])) if isinstance(test, dict) else 0
        msg = f"Настройки сохранены. Тест: найдено {count} результатов."
    else:
        msg = "Настройки поиска сохранены"
    return RedirectResponse("/portal/search?message=" + urllib.parse.quote(msg),
                            status_code=303)


# ── Рабочее пространство (календарь, задачи, заметки, Obsidian) ─────────

VIKUNJA_URL = os.environ.get("VIKUNJA_BASE_URL", "http://vikunja:3456").rstrip("/")
VIKUNJA_TOKEN = os.environ.get("VIKUNJA_API_TOKEN", "")


def _nav(active: str, session: dict) -> str:
    items = [
        ("workspace", "/portal/workspace", "Главная"),
        ("calendar", "/portal/calendar", "Календарь"),
        ("tasks", "/portal/tasks", "Задачи"),
        ("notes", "/portal/notes", "Заметки"),
        ("obsidian", "/portal/obsidian", "Obsidian"),
        ("search", "/portal/search", "Поиск"),
    ]
    parts = []
    for key, href, label in items:
        cls = "tab on" if key == active else "tab"
        parts.append('<a class="' + cls + '" href="' + href + '">' + esc(label) + '</a>')
    admin = ""
    if OWNER and session.get("u") == OWNER:
        admin = ('<a class="tab" href="/portal/admin/status">Устройство</a>'
                 '<a class="tab" href="/portal/admin/llm">Провайдер</a>')
    return ('<nav class="tabs"><a class="tab" href="/">Чат</a>' + "".join(parts)
            + '<a class="tab" href="/portal/llm">Нейросеть</a>'
            + '<a class="tab" href="/portal/account">Настройки</a>'
            + admin + '<a class="tab" href="/portal/logout">Выход</a></nav>')


def ws_page(title: str, body: str, session: dict, active: str,
            message: str = "", error: bool = False) -> HTMLResponse:
    notice = ""
    if message:
        color = "#fb4934" if error else "#b8bb26"
        notice = '<div class="notice" style="border-color:' + color + '">' + esc(message) + '</div>'
    nav = _nav(active, session)
    html = """<!doctype html>
<html lang="ru"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>""" + esc(title) + """ — OmniSecretary</title>
<style>
:root { --bg:#282828; --bg0:#1d2021; --bg1:#3c3836; --bg2:#504945; --bg3:#665c54;
        --fg:#ebdbb2; --fg2:#d5c4a1; --gray:#928374; --yellow:#fabd2f; --orange:#fe8019;
        --green:#b8bb26; --red:#fb4934; --blue:#83a598; --aqua:#8ec07c; --purple:#d3869b; }
* { box-sizing:border-box; }
body { margin:0; color:var(--fg); font:15px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif;
  background:linear-gradient(145deg,#1d2021 0%,#282828 45%,#32302f 100%); min-height:100vh; }
.tabs { display:flex; flex-wrap:wrap; gap:6px; padding:14px 18px; border-bottom:1px solid var(--bg2);
  position:sticky; top:0; background:rgba(29,32,33,.92); backdrop-filter:blur(8px); z-index:10; }
.tab { padding:8px 12px; border-radius:10px; color:var(--fg2); text-decoration:none; font-size:13px;
  border:1px solid transparent; }
.tab:hover { background:var(--bg1); color:var(--fg); }
.tab.on { background:rgba(250,189,47,.12); color:var(--yellow); border-color:rgba(250,189,47,.25); }
.wrap { max-width:1100px; margin:0 auto; padding:22px 18px 40px; }
h1 { font-size:26px; margin:0 0 6px; }
h2 { font-size:16px; margin:22px 0 10px; color:var(--yellow); }
p.sub { color:var(--gray); margin:0 0 18px; }
.grid { display:grid; gap:14px; }
@media(min-width:860px) { .grid.cols2 { grid-template-columns:1fr 1fr; } }
.card { background:linear-gradient(180deg,rgba(60,56,54,.9),rgba(40,40,40,.9));
  border:1px solid var(--bg2); border-radius:16px; padding:18px; }
.notice { margin:0 0 16px; padding:12px 14px; border-left:3px solid var(--green);
  background:rgba(40,40,40,.7); font-size:13px; border-radius:0 8px 8px 0; }
label { display:block; font-size:12px; color:#bdae93; margin:12px 0 6px; }
input,select,textarea { width:100%; padding:10px 12px; border-radius:10px; border:1px solid var(--bg2);
  background:var(--bg0); color:var(--fg); font:14px/1.4 system-ui,sans-serif; }
textarea { min-height:110px; resize:vertical; }
button,.btn { display:inline-block; margin-top:10px; padding:10px 14px; border:0; border-radius:10px;
  background:linear-gradient(135deg,var(--yellow),var(--orange)); color:#282828; font:700 13px/1 system-ui;
  cursor:pointer; text-decoration:none; }
button.sec,.btn.sec { background:var(--bg2); color:var(--fg); border:1px solid var(--bg3); }
table { width:100%; border-collapse:collapse; font-size:14px; }
th,td { text-align:left; padding:10px 8px; border-bottom:1px solid var(--bg2); vertical-align:top; }
th { color:var(--gray); font-weight:500; font-size:12px; text-transform:uppercase; letter-spacing:.5px; }
a { color:var(--blue); }
.dim { color:var(--gray); }
.row { display:flex; gap:10px; flex-wrap:wrap; align-items:end; }
.row > * { flex:1; min-width:140px; }
.pill { display:inline-block; padding:3px 8px; border-radius:999px; font-size:12px; background:var(--bg2); }
.ok { color:var(--green); } .bad { color:var(--red); }
.cal { display:grid; grid-template-columns:repeat(7,1fr); gap:6px; }
.day { background:var(--bg1); border:1px solid var(--bg2); border-radius:10px; min-height:72px; padding:8px; }
.day.muted { opacity:.45; }
.day.today { border-color:var(--yellow); }
.day .n { font-weight:700; font-size:13px; }
.dot { display:inline-block; width:7px; height:7px; border-radius:50%; background:var(--yellow); margin-right:4px; }
.ev { font-size:12px; margin-top:4px; color:var(--fg2); overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
ul { padding-left:18px; } li { margin:4px 0; }
/* Блоки кода: единая тёмная палитра без белых подложек highlighter. */
:is(pre,code,pre *,code *) { background:transparent !important; }
:is(pre,code) { color:#ebdbb2; font-family:"Noto Sans Mono",ui-monospace,monospace; font-size:.9em; }
:not(pre) > code { padding:2px 6px; border:1px solid var(--bg2); border-radius:6px;
  background:var(--bg0) !important; }
pre { white-space:pre; padding:12px 14px; overflow-x:auto; border:1px solid var(--bg2);
  border-radius:10px; background:var(--bg0) !important; }
pre code { display:block; padding:0; border:0; }
pre ::selection { background:var(--bg3); color:#fbf1c7; }
</style></head><body>
""" + nav + """
<main class="wrap">""" + notice + body + """</main>
</body></html>"""
    return HTMLResponse(html)


def _vikunja(method: str, path: str, body: dict | None = None, params: dict | None = None):
    if not VIKUNJA_TOKEN:
        return None
    try:
        with httpx.Client(timeout=20) as client:
            r = client.request(method, VIKUNJA_URL + path, json=body, params=params,
                               headers={"Authorization": f"Bearer {VIKUNJA_TOKEN}"})
        if r.status_code >= 400:
            return None
        return r.json() if r.content else {}
    except Exception:  # noqa: BLE001
        return None


def _month_grid(year: int, month: int):
    import calendar
    cal = calendar.Calendar(firstweekday=0)
    return list(cal.itermonthdates(year, month))


@app.get("/workspace", response_class=HTMLResponse)
def workspace(request: Request):
    session = read_session(request)
    if not session:
        return RedirectResponse("/portal/login", status_code=303)
    token = session.get("t", "")
    today = datetime.now()
    start = today.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=7)
    evs, notes, tasks = [], [], []
    try:
        evs = owui_call(token, "GET", "/api/v1/calendars/events", params={
            "start": start.strftime("%Y-%m-%dT%H:%M:%S"),
            "end": end.strftime("%Y-%m-%dT%H:%M:%S")}) or []
        notes = owui_call(token, "GET", "/api/v1/notes/") or []
    except Exception:
        pass
    try:
        t = _vikunja("GET", "/api/v1/tasks/all", params={"filter": "done = false"}) or []
        tasks = t if isinstance(t, list) else []
    except Exception:
        pass
    ev_html = "".join('<li>' + datetime.fromtimestamp(e["start_at"]/1e9).strftime('%d.%m %H:%M') + ' — ' + esc(e.get("title","")) + '</li>'
                      for e in sorted(evs, key=lambda x: x.get("start_at",0))[:8]) or '<li class="dim">Нет событий на неделю</li>'
    task_html = "".join('<li>' + ("✓ " if x.get("done") else "• ") + esc(x.get("title","")) + '</li>' for x in tasks[:8]) or '<li class="dim">Нет активных задач</li>'
    note_html = "".join('<li>' + esc(n.get("title","без названия")) + '</li>' for n in notes[:8]) or '<li class="dim">Заметок пока нет</li>'
    body = (
        '<h1>Рабочее пространство</h1>'
        '<p class="sub">Календарь, задачи, заметки и Obsidian — в одном месте. Пользователь: <b>' + esc(session.get('u','')) + '</b></p>'
        '<div class="grid cols2">'
        '<div class="card"><h2>Ближайшие события</h2><ul>' + ev_html + '</ul><a class="btn" href="/portal/calendar">Открыть календарь</a></div>'
        '<div class="card"><h2>Задачи</h2><ul>' + task_html + '</ul><a class="btn" href="/portal/tasks">Открыть задачи</a></div>'
        '<div class="card"><h2>Заметки</h2><ul>' + note_html + '</ul><a class="btn" href="/portal/notes">Открыть заметки</a></div>'
        '<div class="card"><h2>Obsidian</h2><p>Персональный вольт: подключите репозиторий GitHub и настройте права по папкам. Нейросеть видит и меняет только то, что вы разрешили.</p>'
        '<a class="btn" href="/portal/obsidian">Открыть Obsidian</a></div>'
        '</div>'
        '<div class="card" style="margin-top:14px"><h2>Поиск и ассистент</h2><ul>'
        '<li>Веб-поиск в чате включён на уровне системы (DuckDuckGo). В диалоге с моделями, поддерживающими инструменты, ассистент может искать в интернете; можно попросить: «найди в интернете…».</li>'
        '<li>Календарём, задачами и заметками удобно пользоваться в разделах выше; в чате ассистент поможет сформулировать или напомнить.</li>'
        '<li>Личный провайдер и API-ключ настраиваются в разделе <a href="/portal/llm">Нейросеть</a>.</li>'
        '</ul><a class="btn sec" href="/">Открыть чат</a></div>'
    )
    return ws_page("Рабочее пространство", body, session, "workspace")


@app.get("/calendar", response_class=HTMLResponse)
def calendar_page(request: Request, y: int = 0, m: int = 0, d: int = 0,
                  message: str = "", error: int = 0):
    session = read_session(request)
    if not session:
        return RedirectResponse("/portal/login", status_code=303)
    token = session.get("t", "")
    today = datetime.now()
    year = y or today.year
    month = m or today.month
    sel = d or today.day
    try:
        sel_date = datetime(year, month, min(sel, 28))
    except Exception:
        year, month, sel = today.year, today.month, today.day
    start = datetime(year, month, 1)
    end = datetime(year + 1, 1, 1) if month == 12 else datetime(year, month + 1, 1)
    events = []
    try:
        events = owui_call(token, "GET", "/api/v1/calendars/events", params={
            "start": (start - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S"),
            "end": (end + timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S")}) or []
    except Exception as exc:
        message = message or f"Не удалось загрузить события: {type(exc).__name__}"
        error = 1
    by_day = {}
    for e in events:
        try:
            dt = datetime.fromtimestamp(e.get("start_at", 0) / 1e9)
            by_day.setdefault(dt.strftime("%Y-%m-%d"), []).append(e)
        except Exception:
            pass
    days = _month_grid(year, month)
    cells = []
    for day in days:
        key = day.strftime("%Y-%m-%d")
        cls = "day"
        if day.month != month:
            cls += " muted"
        if day == today:
            cls += " today"
        evs = by_day.get(key, [])
        dots = "".join('<div class="ev"><span class="dot"></span>' + esc(x.get("title",""))[:18] + '</div>' for x in evs[:2])
        href = "/portal/calendar?y=%d&m=%d&d=%d" % (year, month, day.day)
        cells.append('<a href="' + href + '" style="text-decoration:none;color:inherit"><div class="' + cls + '"><div class="n">' + str(day.day) + '</div>' + dots + '</div></a>')
    prev_m = month - 1 or 12
    prev_y = year - 1 if month == 1 else year
    next_m = month % 12 + 1
    next_y = year + 1 if month == 12 else year
    sel_key = "%04d-%02d-%02d" % (year, month, sel)
    day_events = sorted(by_day.get(sel_key, []), key=lambda x: x.get("start_at", 0))
    rows = []
    for e in day_events:
        dt = datetime.fromtimestamp(e.get("start_at", 0) / 1e9)
        rows.append('<tr><td>' + dt.strftime('%H:%M') + '</td><td>' + esc(e.get("title","")) + '</td><td class="dim">' + esc(e.get("description") or "") + '</td>'
                    + '<td><form method="post" action="/portal/calendar/delete"><input type="hidden" name="csrf" value="' + csrf_token() + '">'
                    + '<input type="hidden" name="id" value="' + esc(str(e.get("id",""))) + '">'
                    + '<input type="hidden" name="y" value="' + str(year) + '"><input type="hidden" name="m" value="' + str(month) + '"><input type="hidden" name="d" value="' + str(sel) + '">'
                    + '<button class="sec" type="submit">Удалить</button></form></td></tr>')
    table = ("<table><tr><th>Время</th><th>Событие</th><th>Описание</th><th></th></tr>" + "".join(rows) + "</table>") if rows else '<p class="dim">На выбранный день событий нет.</p>'
    month_names = ["","январь","февраль","март","апрель","май","июнь","июль","август","сентябрь","октябрь","ноябрь","декабрь"]
    body = (
        '<h1>Календарь</h1><p class="sub">Листайте месяцы, выбирайте дату и управляйте событиями.</p>'
        '<div class="row" style="margin-bottom:12px;align-items:center">'
        '<a class="btn sec" href="/portal/calendar?y=%d&m=%d&d=1">← %s</a>' % (prev_y, prev_m, month_names[prev_m])
        + '<div style="text-align:center;font-weight:700;font-size:18px">' + month_names[month] + ' ' + str(year) + '</div>'
        + '<a class="btn sec" href="/portal/calendar?y=%d&m=%d&d=1">%s →</a>' % (next_y, next_m, month_names[next_m])
        + '</div>'
        '<div class="cal" style="margin-bottom:8px;color:var(--gray);font-size:12px;text-align:center"><div>Пн</div><div>Вт</div><div>Ср</div><div>Чт</div><div>Пт</div><div>Сб</div><div>Вс</div></div>'
        '<div class="cal">' + "".join(cells) + '</div>'
        '<div class="card" style="margin-top:16px"><h2 style="margin-top:0">События: %02d.%02d.%d</h2>' % (sel, month, year)
        + table
        + '<h2>Добавить событие</h2><form method="post" action="/portal/calendar/event">'
        '<input type="hidden" name="csrf" value="' + csrf_token() + '">'
        '<input type="hidden" name="y" value="' + str(year) + '"><input type="hidden" name="m" value="' + str(month) + '"><input type="hidden" name="d" value="' + str(sel) + '">'
        '<div class="row"><div><label>Название</label><input name="title" required placeholder="Встреча, звонок…"></div></div>'
        '<div class="row"><div><label>Дата</label><input type="date" name="date" value="%04d-%02d-%02d" required></div>' % (year, month, sel)
        + '<div><label>Время</label><input type="time" name="time" value="10:00" required></div>'
        + '<div><label>Длительность</label><select name="minutes"><option value="30">30 мин</option><option value="60" selected>1 час</option><option value="120">2 часа</option></select></div></div>'
        '<label>Описание</label><input name="description" placeholder="необязательно">'
        '<button type="submit">Добавить событие</button></form></div>'
    )
    return ws_page("Календарь", body, session, "calendar", message, bool(error))


@app.post("/calendar/event", response_class=HTMLResponse)
def calendar_event(request: Request, title: str = Form(""), date: str = Form(""),
                   time: str = Form("10:00"), minutes: int = Form(60),
                   description: str = Form(""), csrf: str = Form(""),
                   y: int = Form(0), m: int = Form(0), d: int = Form(0)):
    session = read_session(request)
    if not session:
        return RedirectResponse("/portal/login", status_code=303)
    if not csrf_ok(csrf):
        return RedirectResponse("/portal/calendar?y=%d&m=%d&d=%d&error=1&message=Форма+устарела" % (y, m, d), status_code=303)
    try:
        token = session.get("t", "")
        moment = datetime.strptime(date + " " + time, "%Y-%m-%d %H:%M")
        calendars = owui_call(token, "GET", "/api/v1/calendars/")
        calendar_id = calendars[0]["id"]
        owui_call(token, "POST", "/api/v1/calendars/events/create", body={
            "calendar_id": calendar_id, "title": title.strip(),
            "description": description.strip(), "location": "", "all_day": False,
            "start_at": int(moment.timestamp() * 1e9),
            "end_at": int((moment + timedelta(minutes=int(minutes))).timestamp() * 1e9),
        })
        return RedirectResponse("/portal/calendar?y=%d&m=%d&d=%d&message=Событие+добавлено" % (y, m, d), status_code=303)
    except Exception as exc:
        return RedirectResponse("/portal/calendar?y=%d&m=%d&d=%d&error=1&message=Ошибка" % (y, m, d), status_code=303)


@app.post("/calendar/delete", response_class=HTMLResponse)
def calendar_delete(request: Request, id: str = Form(""), csrf: str = Form(""),
                    y: int = Form(0), m: int = Form(0), d: int = Form(0)):
    session = read_session(request)
    if not session:
        return RedirectResponse("/portal/login", status_code=303)
    if csrf_ok(csrf):
        try:
            owui_call(session.get("t", ""), "DELETE", "/api/v1/calendars/events/" + id + "/delete")
        except Exception:
            pass
    return RedirectResponse("/portal/calendar?y=%d&m=%d&d=%d" % (y, m, d), status_code=303)


@app.get("/tasks", response_class=HTMLResponse)
def tasks_page(request: Request, message: str = "", error: int = 0):
    session = read_session(request)
    if not session:
        return RedirectResponse("/portal/login", status_code=303)
    tasks = _vikunja("GET", "/api/v1/tasks/all", params={"filter": "done = false"}) or []
    done_tasks = _vikunja("GET", "/api/v1/tasks/all", params={"filter": "done = true"}) or []
    if not isinstance(tasks, list):
        tasks = []
    if not isinstance(done_tasks, list):
        done_tasks = []
    rows = []
    for t in tasks[:100]:
        due = t.get("due_date") or ""
        if isinstance(due, str) and due.startswith("0001"):
            due = ""
        rows.append('<tr><td>' + esc(t.get("title","")) + '</td><td class="dim">' + esc(str(due)[:16]) + '</td>'
                    + '<td><form method="post" action="/portal/tasks/done" style="display:inline"><input type="hidden" name="csrf" value="' + csrf_token() + '">'
                    + '<input type="hidden" name="id" value="' + str(t.get("id")) + '"><button class="sec" type="submit">Готово</button></form> '
                    + '<form method="post" action="/portal/tasks/delete" style="display:inline"><input type="hidden" name="csrf" value="' + csrf_token() + '">'
                    + '<input type="hidden" name="id" value="' + str(t.get("id")) + '"><button class="sec" type="submit">Удалить</button></form></td></tr>')
    table = ("<table><tr><th>Задача</th><th>Срок</th><th></th></tr>" + "".join(rows) + "</table>") if rows else '<p class="dim">Активных задач нет.</p>'
    done_rows = "".join('<li class="dim">✓ ' + esc(t.get("title","")) + '</li>' for t in done_tasks[:20]) or '<li class="dim">Нет выполненных</li>'
    warn = "" if VIKUNJA_TOKEN else '<div class="notice" style="border-color:#fabd2f">Vikunja не настроен (нет API-токена на сервере). Задачи недоступны.</div>'
    body = (
        '<h1>Задачи</h1><p class="sub">Список дел в Vikunja. Отмечайте выполненные и добавляйте новые.</p>' + warn
        + '<div class="card"><h2 style="margin-top:0">Активные</h2>' + table
        + '<h2>Новая задача</h2><form method="post" action="/portal/tasks/create"><input type="hidden" name="csrf" value="' + csrf_token() + '">'
        + '<div class="row"><div><label>Название</label><input name="title" required placeholder="Что нужно сделать"></div>'
        + '<div><label>Срок (необязательно)</label><input type="datetime-local" name="due"></div></div>'
        + '<label>Описание</label><textarea name="description" placeholder="подробности"></textarea>'
        + '<button type="submit">Добавить задачу</button></form></div>'
        + '<div class="card" style="margin-top:14px"><h2 style="margin-top:0">Недавно выполненные</h2><ul>' + done_rows + '</ul></div>'
    )
    return ws_page("Задачи", body, session, "tasks", message, bool(error))


@app.post("/tasks/create", response_class=HTMLResponse)
def tasks_create(request: Request, title: str = Form(""), description: str = Form(""),
                 due: str = Form(""), csrf: str = Form("")):
    session = read_session(request)
    if not session:
        return RedirectResponse("/portal/login", status_code=303)
    if not csrf_ok(csrf):
        return RedirectResponse("/portal/tasks?error=1&message=Форма+устарела", status_code=303)
    payload = {"title": title.strip(), "description": description.strip()}
    if due.strip():
        payload["due_date"] = due.strip() + ":00Z" if len(due.strip()) == 16 else due.strip()
    r = _vikunja("PUT", "/api/v1/projects/1/tasks", body=payload)
    if r is None:
        return RedirectResponse("/portal/tasks?error=1&message=Не+удалось+создать+задачу", status_code=303)
    return RedirectResponse("/portal/tasks?message=Задача+добавлена", status_code=303)


@app.post("/tasks/done", response_class=HTMLResponse)
def tasks_done(request: Request, id: int = Form(0), csrf: str = Form("")):
    session = read_session(request)
    if not session:
        return RedirectResponse("/portal/login", status_code=303)
    if csrf_ok(csrf) and id:
        _vikunja("POST", "/api/v1/tasks/" + str(id), body={"done": True})
    return RedirectResponse("/portal/tasks", status_code=303)


@app.post("/tasks/delete", response_class=HTMLResponse)
def tasks_delete(request: Request, id: int = Form(0), csrf: str = Form("")):
    session = read_session(request)
    if not session:
        return RedirectResponse("/portal/login", status_code=303)
    if csrf_ok(csrf) and id:
        _vikunja("DELETE", "/api/v1/tasks/" + str(id))
    return RedirectResponse("/portal/tasks", status_code=303)


@app.get("/notes", response_class=HTMLResponse)
def notes_page(request: Request, note: str = "", message: str = "", error: int = 0):
    session = read_session(request)
    if not session:
        return RedirectResponse("/portal/login", status_code=303)
    token = session.get("t", "")
    notes = []
    try:
        notes = owui_call(token, "GET", "/api/v1/notes/") or []
    except Exception as exc:
        message = message or f"Не удалось загрузить заметки: {type(exc).__name__}"
        error = 1
    selected = None
    if note:
        for n in notes:
            if n.get("id") == note:
                selected = n
                break
        if not selected:
            try:
                selected = owui_call(token, "GET", "/api/v1/notes/" + note)
            except Exception:
                selected = None
    items = []
    for n in notes[:100]:
        mark = " <b>(открыта)</b>" if n.get("id") == note else ""
        items.append('<li><a href="/portal/notes?note=' + esc(n.get("id","")) + '">' + esc(n.get("title","без названия")) + '</a>' + mark + '</li>')
    list_html = "<ul>" + "".join(items) + "</ul>" if items else '<p class="dim">Заметок пока нет. Создайте первую справа.</p>'
    view = ""
    if selected:
        content = ((selected.get("data") or {}).get("content") or {}).get("md", "")
        view = ('<div class="card"><h2 style="margin-top:0">' + esc(selected.get("title","")) + '</h2>'
                + '<pre style="white-space:pre-wrap;font-family:inherit;color:var(--fg2)">' + esc(content) + '</pre>'
                + '<form method="post" action="/portal/notes/delete"><input type="hidden" name="csrf" value="' + csrf_token() + '">'
                + '<input type="hidden" name="id" value="' + esc(selected.get("id","")) + '">'
                + '<button class="sec" type="submit">Удалить заметку</button></form></div>')
    body = (
        '<h1>Заметки</h1><p class="sub">Заметки Open WebUI — хранятся на сервере в вашей учётной записи, доступны в вебе и приложении.</p>'
        '<div class="grid cols2"><div class="card"><h2 style="margin-top:0">Список</h2>' + list_html + '</div><div>'
        + (view or '<div class="card"><p class="dim">Выберите заметку слева, чтобы прочитать.</p></div>')
        + '<div class="card" style="margin-top:14px"><h2 style="margin-top:0">Новая заметка</h2>'
        + '<form method="post" action="/portal/notes/create"><input type="hidden" name="csrf" value="' + csrf_token() + '">'
        + '<label>Заголовок</label><input name="title" required placeholder="Название заметки">'
        + '<label>Текст</label><textarea name="text" placeholder="Содержание (Markdown)" style="min-height:180px"></textarea>'
        + '<button type="submit">Сохранить заметку</button></form></div></div></div>'
    )
    return ws_page("Заметки", body, session, "notes", message, bool(error))


@app.post("/notes/create", response_class=HTMLResponse)
def notes_create(request: Request, title: str = Form(""), text: str = Form(""), csrf: str = Form("")):
    session = read_session(request)
    if not session:
        return RedirectResponse("/portal/login", status_code=303)
    if not csrf_ok(csrf):
        return RedirectResponse("/portal/notes?error=1&message=Форма+устарела", status_code=303)
    try:
        owui_call(session.get("t", ""), "POST", "/api/v1/notes/create", body={
            "title": title.strip(), "data": {"content": {"md": text}}})
        return RedirectResponse("/portal/notes?message=Заметка+сохранена", status_code=303)
    except Exception:
        return RedirectResponse("/portal/notes?error=1&message=Ошибка+сохранения", status_code=303)


@app.post("/notes/delete", response_class=HTMLResponse)
def notes_delete(request: Request, id: str = Form(""), csrf: str = Form("")):
    session = read_session(request)
    if not session:
        return RedirectResponse("/portal/login", status_code=303)
    if csrf_ok(csrf):
        try:
            owui_call(session.get("t", ""), "DELETE", "/api/v1/notes/" + id + "/delete")
        except Exception:
            pass
    return RedirectResponse("/portal/notes", status_code=303)


@app.get("/obsidian", response_class=HTMLResponse)
def obsidian_page(request: Request, path: str = "", q: str = "", message: str = "", error: int = 0):
    session = read_session(request)
    if not session:
        return RedirectResponse("/portal/login", status_code=303)
    uid = session.get("uid", "")
    cfg = {}
    try:
        cfg = pvaults.public(uid) or {}
    except Exception:
        cfg = {}
    configured = cfg.get("configured")
    status = cfg.get("status") or "не настроен"
    body_top = (
        '<h1>Obsidian</h1><p class="sub">Персональный вольт в вашем GitHub. Доступ нейросети ограничен правилами по папкам, которые задаёте вы.</p>'
        '<div class="card"><h2 style="margin-top:0">Состояние</h2>'
        '<p>Подключён: <b>' + ('да' if configured else 'нет') + '</b> · Статус: <span class="pill">' + esc(str(status)) + '</span></p>'
        '<p class="dim">Настройка репозитория, PAT и прав по папкам — в приложении (Заметки → Obsidian). Ниже — просмотр и поиск, если вольт подключён и синхронизирован.</p></div>'
    )
    browser = ""
    if configured:
        try:
            files = pvaults.list_files(uid, path or "")
            if files is not None:
                flist = files if isinstance(files, list) else files.get("files", [])
                items = []
                for f in flist[:200]:
                    icon = "📁" if f.get("type") == "dir" else "📄"
                    fpath = f.get("path", "")
                    items.append('<li>' + icon + ' <a href="/portal/obsidian?path=' + urllib.parse.quote(fpath) + '">' + esc(f.get("name","")) + '</a></li>')
                browser = '<div class="card" style="margin-top:14px"><h2 style="margin-top:0">Файлы: /' + esc(path) + '</h2><ul>' + ("".join(items) or '<li class="dim">пусто</li>') + '</ul></div>'
            if path and path.endswith('.md'):
                content = pvaults.read_file(uid, path)
                if content and isinstance(content, dict):
                    txt = content.get("content") or content.get("text") or ""
                    browser += '<div class="card" style="margin-top:14px"><h2 style="margin-top:0">' + esc(path) + '</h2><pre style="white-space:pre-wrap">' + esc(txt[:12000]) + '</pre></div>'
        except Exception as exc:
            browser = '<div class="card" style="margin-top:14px"><p class="bad">Не удалось открыть вольт: ' + esc(type(exc).__name__) + '. Синхронизируйте и проверьте права доступа.</p></div>'
    search_block = ""
    if configured:
        results = []
        if q:
            try:
                results = pvaults.search(uid, q) or []
                if isinstance(results, dict):
                    results = results.get("results", [])
            except Exception:
                results = []
        if q:
            if results:
                res_html = "".join('<li><a href="/portal/obsidian?path=' + urllib.parse.quote(r.get("path","")) + '">' + esc(r.get("path","")) + '</a> <span class="dim">' + esc((r.get("snippet") or "")[:120]) + '</span></li>' for r in results[:30])
                res_block = '<ul>' + res_html + '</ul>'
            else:
                res_block = '<p class="dim">Ничего не найдено.</p>'
        else:
            res_block = '<p class="dim">Введите запрос для поиска.</p>'
        search_block = ('<div class="card" style="margin-top:14px"><h2 style="margin-top:0">Поиск по вольту</h2>'
                        + '<form method="get" action="/portal/obsidian"><div class="row"><div><label>Запрос</label><input name="q" value="' + esc(q) + '" placeholder="слово или фраза"></div>'
                        + '<div style="flex:0"><button type="submit">Найти</button></div></div></form>' + res_block + '</div>')
    body = body_top + browser + search_block
    return ws_page("Obsidian", body, session, "obsidian", message, bool(error))


# Инициализация хранилища LLM-провайдеров и конфига шлюза.
try:
    llm.db().close()
    llm.write_gateway_config()
except Exception:  # noqa: BLE001
    pass


# ── Мобильный API ────────────────────────────────────────────────────────
# Отдельный модуль: клиент приложения ходит с Bearer-токеном Open WebUI.
from mobile_api import router as mobile_router  # noqa: E402

app.include_router(mobile_router)




@app.get("/portal/obsidian-setup")
def portal_obsidian_setup(request: Request):
    """Форма подключения персонального Obsidian-вольта."""
    sess = session(request) if callable(globals().get("session")) else None
    if not sess:
        return RedirectResponse("/portal/login", status_code=303)
    user = sess.get("user") if isinstance(sess.get("user"), dict) else {}
    uid = str(sess.get("id") or sess.get("user_id") or user.get("id") or "me")
    cfg_path = Path("/srv/omnisecretary/vaults") / uid / "portal-config.json"
    try:
        cfg = json.loads(cfg_path.read_text()) if cfg_path.exists() else {}
    except Exception:
        cfg = {}
    def v(key):
        val = cfg.get(key, "")
        return esc(str(val)) if callable(globals().get("esc")) else str(val)
    repo, branch, token = v("repo"), v("branch") or "main", v("token")
    perms = cfg.get("permissions") or {}
    perm_rows = ""
    for path, flags in perms.items():
        boxes = "".join(
            '<label><input type="checkbox" name="perm_' + esc(str(path)) + '_' + k + '"' + (' checked' if flags.get(k) else '') + '>' + k + '</label>'
            for k in ["read", "list", "search", "create", "append", "update", "delete"]
        )
        perm_rows += '<div class="perm-row"><b>' + esc(str(path)) + '</b>' + boxes + '</div>'
    body = """
    <h1>Настройка Obsidian-вольта</h1>
    <p class="muted">Подключите свой приватный репозиторий и ключ GitHub. Права проверяются сервером при каждом действии.</p>
    <form method="post" action="/portal/obsidian-setup" class="card">
      <label>URL репозитория<input name="repo" value="__REPO__" placeholder="https://github.com/user/vault" required></label>
      <label>Ветка<input name="branch" value="__BRANCH__"></label>
      <label>GitHub PAT<input name="token" type="password" value="__TOKEN__" placeholder="ghp_..."></label>
      <h2>Права по папкам</h2>
      __PERMS__
      <label>Новая папка<input name="new_path" placeholder="AI/"></label>
      <label>Права для новой папки (через запятую)<input name="new_perms" value="read,list,search,create,append"></label>
      <button type="submit">Сохранить и подключить</button>
    </form>
    """.replace("__REPO__", repo).replace("__BRANCH__", branch).replace("__TOKEN__", token).replace("__PERMS__", perm_rows)
    if callable(globals().get("page")):
        return page("Obsidian — настройка", body, request)
    return HTMLResponse("<html><body>" + body + "</body></html>")


@app.post("/portal/obsidian-setup")
async def portal_obsidian_setup_save(request: Request):
    sess = session(request) if callable(globals().get("session")) else None
    if not sess:
        return RedirectResponse("/portal/login", status_code=303)
    user = sess.get("user") if isinstance(sess.get("user"), dict) else {}
    uid = str(sess.get("id") or sess.get("user_id") or user.get("id") or "me")
    form = await request.form()
    cfg_path = Path("/srv/omnisecretary/vaults") / uid / "portal-config.json"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg = {
        "repo": str(form.get("repo", "")).strip(),
        "branch": str(form.get("branch", "main")).strip() or "main",
        "token": str(form.get("token", "")).strip(),
        "permissions": {},
    }
    new_path = str(form.get("new_path", "")).strip()
    new_perms = str(form.get("new_perms", "")).split(",")
    if new_path:
        cfg["permissions"][new_path] = {k.strip(): True for k in new_perms if k.strip()}
    for key, value in form.multi_items():
        if key.startswith("perm_"):
            parts = key.split("_")
            if len(parts) >= 3:
                flag = parts[-1]
                path = "_".join(parts[1:-1])
                if value:
                    cfg["permissions"].setdefault(path, {})[flag] = True
    cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2))
    try:
        cfg_path.chmod(0o600)
    except Exception:
        pass
    return RedirectResponse("/portal/obsidian?saved=1", status_code=303)
