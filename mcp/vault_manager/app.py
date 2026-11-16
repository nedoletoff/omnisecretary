#!/usr/bin/env python3
"""Веб-форма подключения Obsidian-вольта.

Владелец вводит URL приватного репозитория и токен; сервис клонирует вольт
в ${DATA_ROOT}/vault, сохраняет токен в secrets (600) и дальше синхронизирует
его по кнопке. Токен никогда не показывается и не логируется.

Доступ — HTTP Basic (логин/пароль из secrets). Наружу не публикуется:
nginx отдаёт /vault/ только в LAN.
"""

from __future__ import annotations

import base64
import os
import secrets
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

VAULT_DIR = Path(os.environ.get("VAULT_DIR", "/vault"))
SECRETS_DIR = Path(os.environ.get("SECRETS_DIR", "/secrets"))
TOKEN_FILE = SECRETS_DIR / "vault-git-token"
BRANCH_DEFAULT = os.environ.get("VAULT_BRANCH", "main")
ADMIN_USER = os.environ.get("VAULT_ADMIN_USER", "owner")
ADMIN_PASSWORD = os.environ.get("VAULT_ADMIN_PASSWORD", "")

app = FastAPI(title="OmniSecretary vault", docs_url=None, redoc_url=None)
security = HTTPBasic()


def auth(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    if not ADMIN_PASSWORD:
        raise HTTPException(503, "VAULT_ADMIN_PASSWORD не задан")
    ok_user = secrets.compare_digest(credentials.username, ADMIN_USER)
    ok_password = secrets.compare_digest(credentials.password, ADMIN_PASSWORD)
    if not (ok_user and ok_password):
        raise HTTPException(401, "неверный логин или пароль", headers={"WWW-Authenticate": "Basic"})
    return credentials.username


def token() -> str:
    try:
        return TOKEN_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def clean_repo_url(raw: str) -> str:
    """Проверяет URL репозитория.

    Токен передаётся только через http.extraheader, поэтому логин/пароль
    внутри URL не нужны: они попали бы в argv (`ps`) и в .git/config.
    """
    raw = raw.strip()
    parts = urlsplit(raw)
    if parts.scheme != "https" or not parts.hostname:
        raise ValueError("нужен https-URL")
    if parts.username or parts.password or "@" in parts.netloc:
        raise ValueError("URL не должен содержать логин или токен")
    return raw


def check_csrf(request: Request, value: str) -> None:
    """Double-submit cookie + проверка Origin: форма без cookie не пройдёт."""
    cookie = request.cookies.get("vault_csrf", "")
    if not cookie or not value or not secrets.compare_digest(cookie, value):
        raise HTTPException(403, "форма устарела — обновите страницу")
    origin = request.headers.get("origin") or request.headers.get("referer") or ""
    if origin:
        host = (urlsplit(origin).hostname or "").lower()
        own = (request.headers.get("host", "").split(":")[0] or "").lower()
        if host and own and host != own:
            raise HTTPException(403, "запрос с чужого сайта")


def save_token(value: str) -> None:
    SECRETS_DIR.mkdir(parents=True, exist_ok=True)
    old_umask = os.umask(0o077)
    try:
        TOKEN_FILE.write_text(value.strip() + "\n", encoding="utf-8")
    finally:
        os.umask(old_umask)
    TOKEN_FILE.chmod(0o600)


def git(*args: str, check: bool = False, timeout: int = 300) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    value = token()
    if value:
        basic = base64.b64encode(f"x-access-token:{value}".encode()).decode()
        env["GIT_CONFIG_COUNT"] = "1"
        env["GIT_CONFIG_KEY_0"] = "http.extraheader"
        env["GIT_CONFIG_VALUE_0"] = f"AUTHORIZATION: basic {basic}"
    env["GIT_TERMINAL_PROMPT"] = "0"
    return subprocess.run(
        ["git", "-c", f"safe.directory={VAULT_DIR}", *args],
        cwd=str(VAULT_DIR) if VAULT_DIR.is_dir() else None,
        capture_output=True, text=True, timeout=timeout, env=env,
    )


def is_repo() -> bool:
    return (VAULT_DIR / ".git").is_dir()


def status() -> dict:
    if not is_repo():
        return {"ready": False, "reason": "вольт не подключён"}
    branch = git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    remote = git("remote", "get-url", "origin").stdout.strip()
    dirty = git("status", "--porcelain").stdout.strip().splitlines()
    log = git("log", "--oneline", "-5").stdout.strip().splitlines()
    ahead = git("rev-list", "--count", "@{u}..HEAD").stdout.strip()
    behind = git("rev-list", "--count", "HEAD..@{u}").stdout.strip()
    return {
        "ready": True,
        "branch": branch,
        "remote": remote,
        "token_saved": bool(token()),
        "dirty_files": len(dirty),
        "ahead": ahead,
        "behind": behind,
        "last_commits": log,
    }


PAGE = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>OmniSecretary · вольт Obsidian</title>
<style>
  :root {{ --bg:#282828; --bg1:#3c3836; --fg:#ebdbb2; --gray:#a89984;
           --yellow:#d79921; --green:#b8bb26; --red:#fb4934; --blue:#83a598; }}
  body {{ background:var(--bg); color:var(--fg); font:16px/1.5 ui-monospace,monospace;
         margin:0; padding:2rem; }}
  main {{ max-width:760px; margin:0 auto; }}
  h1 {{ color:var(--yellow); font-size:1.4rem; }}
  fieldset {{ border:1px solid var(--bg1); padding:1rem 1.25rem; margin:0 0 1.5rem; }}
  legend {{ color:var(--yellow); padding:0 .4rem; }}
  label {{ display:block; margin:.75rem 0 .25rem; color:var(--gray); }}
  input {{ width:100%; box-sizing:border-box; background:var(--bg1); color:var(--fg);
          border:1px solid #504945; padding:.5rem; font:inherit; }}
  button {{ background:var(--yellow); color:#282828; border:0; padding:.55rem 1rem;
           font:inherit; font-weight:700; cursor:pointer; margin-top:1rem; }}
  button.secondary {{ background:var(--bg1); color:var(--fg); }}
  pre {{ background:var(--bg1); padding:1rem; overflow:auto; }}
  .ok {{ color:var(--green); }} .bad {{ color:var(--red); }}
  a {{ color:var(--blue); }}
</style></head>
<body><main>
<h1>Obsidian-вольт</h1>
<p>Приватный git-репозиторий. Доступен только владельцу; ассистент пишет
только в <code>AI/</code> и коммитит каждое изменение.</p>
{message}
<fieldset><legend>Состояние</legend><pre>{status}</pre>
<form method="post" action="./sync"><input type="hidden" name="csrf" value="{csrf}"><button class="secondary">Синхронизировать (pull + push)</button></form>
</fieldset>
<fieldset><legend>Подключить репозиторий</legend>
<form method="post" action="./setup">
  <input type="hidden" name="csrf" value="{csrf}">
  <label>URL репозитория (https)</label>
  <input name="repo_url" placeholder="https://github.com/user/private-vault.git" required>
  <label>Ветка</label>
  <input name="branch" value="{branch}">
  <label>Токен (fine-grained PAT, Contents: read/write). Пусто — оставить прежний.</label>
  <input name="token" type="password" autocomplete="off" placeholder="github_pat_...">
  <button>Подключить</button>
</form></fieldset>
<p>Токен хранится в <code>/srv/omnisecretary/secrets/vault-git-token</code>
(права 600) и не попадает в git.</p>
</main></body></html>"""


MESSAGES = {
    "ok": '<p class="ok">Вольт подключён.</p>',
    "synced": '<p class="ok">Синхронизация выполнена.</p>',
    "bad_url": '<p class="bad">Нужен https-URL репозитория.</p>',
    "clone_failed": '<p class="bad">Клонирование не удалось: проверьте URL и права токена.</p>',
    "fetch_failed": '<p class="bad">Не удалось получить ветку из origin.</p>',
    "not_empty": '<p class="bad">Каталог вольта не пуст и не является git-репозиторием.</p>',
    "not_ready": '<p class="bad">Сначала подключите репозиторий.</p>',
}


def render(message: str = "", branch: str = BRANCH_DEFAULT,
           csrf: str = "") -> HTMLResponse:
    import html
    import json
    response = HTMLResponse(PAGE.format(
        message=message,
        status=html.escape(json.dumps(status(), ensure_ascii=False, indent=2)),
        branch=html.escape(branch),
        csrf=html.escape(csrf),
    ))
    if csrf:
        response.set_cookie("vault_csrf", csrf, max_age=3600,
                            httponly=True, samesite="strict")
    return response


@app.get("/", response_class=HTMLResponse)
def index(request: Request, msg: str = "", _: str = Depends(auth)) -> HTMLResponse:
    return render(MESSAGES.get(msg, ""), csrf=secrets.token_urlsafe(24))


@app.post("/setup")
def setup(request: Request, repo_url: str = Form(...),
          branch: str = Form(BRANCH_DEFAULT),
          token_value: str = Form("", alias="token"), csrf: str = Form(""),
          _: str = Depends(auth)) -> RedirectResponse:
    check_csrf(request, csrf)
    try:
        repo_url = clean_repo_url(repo_url)
    except ValueError:
        return RedirectResponse("./?msg=bad_url", status_code=303)
    if token_value.strip():
        save_token(token_value)

    VAULT_DIR.mkdir(parents=True, exist_ok=True)
    branch = (branch or BRANCH_DEFAULT).strip()
    if is_repo():
        git("remote", "set-url", "origin", repo_url)
        fetch = git("fetch", "origin", branch)
        if fetch.returncode != 0:
            return RedirectResponse("./?msg=fetch_failed", status_code=303)
        git("checkout", branch)
        git("pull", "--rebase", "--autostash", "origin", branch)
    else:
        if any(VAULT_DIR.iterdir()):
            return RedirectResponse("./?msg=not_empty", status_code=303)
        clone = subprocess.run(
            ["git", "clone", "--branch", branch, repo_url, str(VAULT_DIR)],
            capture_output=True, text=True, timeout=600,
            env={**os.environ, **(
                {"GIT_CONFIG_COUNT": "1",
                 "GIT_CONFIG_KEY_0": "http.extraheader",
                 "GIT_CONFIG_VALUE_0": "AUTHORIZATION: basic " +
                 base64.b64encode(f"x-access-token:{token()}".encode()).decode()}
                if token() else {})},
        )
        if clone.returncode != 0:
            return RedirectResponse("./?msg=clone_failed", status_code=303)
    return RedirectResponse("./?msg=ok", status_code=303)


@app.post("/sync")
def sync(request: Request, csrf: str = Form(""),
         _: str = Depends(auth)) -> RedirectResponse:
    check_csrf(request, csrf)
    if not is_repo():
        return RedirectResponse("./?msg=not_ready", status_code=303)
    git("pull", "--rebase", "--autostash")
    git("push")
    return RedirectResponse("./?msg=synced", status_code=303)


@app.get("/status")
def status_json(_: str = Depends(auth)) -> JSONResponse:
    return JSONResponse(status())

@app.get("/healthz")
def healthz() -> JSONResponse:
    return JSONResponse({"status": "ok"})
