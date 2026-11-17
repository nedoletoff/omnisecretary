#!/usr/bin/env python3
"""MCP-сервер вольта Obsidian.

Экономия токенов — главное требование: инструменты отдают минимум текста.
Сначала поиск по FTS5-индексу (пути + короткие сниппеты), потом чтение
одной-двух заметок с обрезкой. Весь вольт в контекст никогда не попадает.

Запись — только в подпапку AI/ (VAULT_WRITE_PREFIX). Каждая запись —
отдельный git-коммит; push выполняется сразу (токен не хранится в git config).

Доступ: сервер зарегистрирован в Open WebUI как admin-only tool server,
плюс проверка JWT пользователя (должен быть владельцем).
"""

from __future__ import annotations

import base64
import json
import os
import sqlite3
import subprocess
import threading
import time
from pathlib import Path

from mcp.server.fastmcp import FastMCP

VAULT_DIR = Path(os.environ.get("VAULT_DIR", "/vault")).resolve()
WRITE_PREFIX = os.environ.get("VAULT_WRITE_PREFIX", "AI").strip("/")
INDEX_PATH = Path(os.environ.get("VAULT_INDEX_PATH", "/index/vault.db"))
TOKEN_FILE = Path(os.environ.get("VAULT_TOKEN_FILE", "/secrets/vault-git-token"))
GIT_TIMEOUT = int(os.environ.get("VAULT_GIT_TIMEOUT", "60"))
MAX_READ_CHARS = 8000
# Push после записи. Отключение оставляет коммиты локальными (pull/push вручную).
PUSH_ENABLED = os.environ.get("VAULT_PUSH_ENABLED", "true").lower() in (
    "1", "true", "yes", "on")

mcp = FastMCP(
    "obsidian-vault",
    host="0.0.0.0",
    port=int(os.environ.get("MCP_PORT", "8000")),
    streamable_http_path="/mcp",
    stateless_http=True,
    instructions=(
        "Вольт Obsidian. Сначала vault_search — он возвращает пути и короткие "
        "сниппеты. Затем vault_read одной найденной заметки. Не читай весь вольт. "
        f"Записывай только в {WRITE_PREFIX}/; каждая запись — git-коммит и push."
    ),
)

_push_lock = threading.Lock()
_push_pending = threading.Event()


# ── Git ─────────────────────────────────────────────────────────────────────


def _token() -> str:
    try:
        return TOKEN_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _git_env() -> dict[str, str]:
    """Токен передаётся через extraheader, а не через remote URL."""
    env = dict(os.environ)
    token = _token()
    if token:
        basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        env["GIT_CONFIG_COUNT"] = "1"
        env["GIT_CONFIG_KEY_0"] = "http.extraheader"
        env["GIT_CONFIG_VALUE_0"] = f"AUTHORIZATION: basic {basic}"
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    return env


def git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(
        [
            "git", "-C", str(VAULT_DIR),
            "-c", f"safe.directory={VAULT_DIR}",
            "-c", "user.name=OmniSecretary",
            "-c", "user.email=ai@omnisecretary.local",
            *args,
        ],
        capture_output=True, text=True, timeout=GIT_TIMEOUT, env=_git_env(),
    )
    if check and result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {result.stderr.strip()[:400]}")
    return result


def _push_loop() -> None:
    """Push в фоне: коммит не ждёт сети, повтор при сбое."""
    while True:
        _push_pending.wait()
        _push_pending.clear()
        time.sleep(2)  # склеиваем серию записей в один push
        with _push_lock:
            result = git("push", check=False)
            if result.returncode != 0:
                # Кто-то (например, Obsidian на телефоне) мог запушить раньше:
                # подтягиваем с rebase и пробуем снова, иначе коммит застрянет.
                pull = git("pull", "--rebase", "--autostash", check=False)
                if pull.returncode == 0:
                    result = git("push", check=False)
                if result.returncode != 0:
                    print(f"[vault] push не удался: {result.stderr.strip()[:300]}", flush=True)


threading.Thread(target=_push_loop, daemon=True).start()


def commit(paths: list[str], message: str) -> str:
    git("add", "--", *paths)
    status = git("status", "--porcelain", "--", *paths)
    if not status.stdout.strip():
        return "изменений нет"
    git("commit", "-m", message)
    if PUSH_ENABLED:
        _push_pending.set()
    sha = git("rev-parse", "--short", "HEAD").stdout.strip()
    return f"коммит {sha}: {message}"


# ── Пути и индекс ───────────────────────────────────────────────────────────


def _safe_path(relative: str, for_write: bool = False) -> Path:
    relative = relative.strip().lstrip("/")
    if not relative:
        raise ValueError("пустой путь")
    target = (VAULT_DIR / relative).resolve()
    if VAULT_DIR not in target.parents and target != VAULT_DIR:
        raise ValueError("путь вне вольта")
    if target.suffix.lower() not in (".md", ".markdown"):
        raise ValueError("только markdown-заметки (.md)")
    if for_write:
        allowed = (VAULT_DIR / WRITE_PREFIX).resolve()
        if allowed not in target.parents:
            raise ValueError(f"запись разрешена только в {WRITE_PREFIX}/")
    return target


def _index_conn() -> sqlite3.Connection:
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(INDEX_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS notes (
            path TEXT PRIMARY KEY,
            mtime REAL NOT NULL,
            size INTEGER NOT NULL
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
            path, body, tokenize='unicode61'
        );
        """
    )
    return conn


def ensure_index() -> int:
    """Инкрементальный индекс: переиндексируем только изменённые файлы."""
    if not VAULT_DIR.is_dir():
        return 0
    conn = _index_conn()
    known = {row[0]: (row[1], row[2]) for row in conn.execute("SELECT path, mtime, size FROM notes")}
    seen = set()
    updated = 0
    for path in VAULT_DIR.rglob("*.md"):
        if any(part.startswith(".") for part in path.relative_to(VAULT_DIR).parts):
            continue
        rel = str(path.relative_to(VAULT_DIR))
        try:
            stat = path.stat()
        except OSError:
            continue
        seen.add(rel)
        if known.get(rel) == (stat.st_mtime, stat.st_size):
            continue
        try:
            body = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        conn.execute("DELETE FROM notes_fts WHERE path = ?", (rel,))
        conn.execute("INSERT INTO notes_fts (path, body) VALUES (?, ?)", (rel, body))
        conn.execute(
            "INSERT INTO notes (path, mtime, size) VALUES (?, ?, ?) "
            "ON CONFLICT(path) DO UPDATE SET mtime = excluded.mtime, size = excluded.size",
            (rel, stat.st_mtime, stat.st_size),
        )
        updated += 1
    for rel in set(known) - seen:
        conn.execute("DELETE FROM notes_fts WHERE path = ?", (rel,))
        conn.execute("DELETE FROM notes WHERE path = ?", (rel,))
    conn.commit()
    conn.close()
    return updated


def reindex_file(rel: str, body: str) -> None:
    conn = _index_conn()
    conn.execute("DELETE FROM notes_fts WHERE path = ?", (rel,))
    conn.execute("INSERT INTO notes_fts (path, body) VALUES (?, ?)", (rel, body))
    conn.commit()
    conn.close()


# ── Инструменты ─────────────────────────────────────────────────────────────


@mcp.tool()
def vault_status() -> str:
    """Состояние вольта: ветка, незакоммиченные изменения, последние коммиты."""
    if not (VAULT_DIR / ".git").is_dir():
        return json.dumps({"ready": False, "reason": "вольт не подключён"}, ensure_ascii=False)
    branch = git("rev-parse", "--abbrev-ref", "HEAD", check=False).stdout.strip()
    dirty = git("status", "--porcelain", check=False).stdout.strip()
    log = git("log", "--oneline", "-5", check=False).stdout.strip().splitlines()
    return json.dumps(
        {
            "ready": True,
            "branch": branch,
            "dirty_files": len(dirty.splitlines()) if dirty else 0,
            "last_commits": log,
            "write_prefix": WRITE_PREFIX + "/",
        },
        ensure_ascii=False,
    )


@mcp.tool()
def vault_search(query: str, limit: int = 5, folder: str = "") -> str:
    """Поиск по заметкам. Возвращает путь, оценку и короткий сниппет.

    Используй ПЕРВЫМ: дешевле, чем чтение файлов. Затем vault_read одной заметки.
    """
    if not (VAULT_DIR / ".git").is_dir():
        return "вольт не подключён"
    ensure_index()
    limit = max(1, min(int(limit), 10))
    sql = (
        "SELECT path, snippet(notes_fts, 1, '«', '»', '…', 14) AS snip, rank "
        "FROM notes_fts WHERE notes_fts MATCH ? "
    )
    params: list = [query]
    if folder:
        sql += "AND path LIKE ? "
        params.append(folder.strip('/') + "/%")
    sql += "ORDER BY rank LIMIT ?"
    params.append(limit)
    conn = _index_conn()
    try:
        rows = conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError as exc:
        return f"ошибка поиска: {exc}"
    finally:
        conn.close()
    if not rows:
        return "ничего не найдено"
    return "\n".join(f"{path} :: {snip}" for path, snip, _rank in rows)


@mcp.tool()
def vault_read(path: str, offset: int = 0, max_chars: int = 4000) -> str:
    """Прочитать одну заметку. По умолчанию не больше 4000 символов."""
    try:
        target = _safe_path(path)
    except ValueError as exc:
        return f"ошибка: {exc}"
    if not target.is_file():
        return f"нет файла: {path}"
    text = target.read_text(encoding="utf-8", errors="replace")
    max_chars = max(500, min(int(max_chars), MAX_READ_CHARS))
    chunk = text[offset:offset + max_chars]
    suffix = "" if offset + max_chars >= len(text) else f"\n…(ещё {len(text) - offset - max_chars} символов)"
    return chunk + suffix


@mcp.tool()
def vault_list(folder: str = "", limit: int = 100) -> str:
    """Список путей заметок (без содержимого) — для навигации."""
    if not (VAULT_DIR / ".git").is_dir():
        return "вольт не подключён"
    limit = max(1, min(int(limit), 500))
    base = VAULT_DIR / folder.strip("/") if folder else VAULT_DIR
    if not base.is_dir():
        return f"нет папки: {folder}"
    paths = []
    for path in sorted(base.rglob("*.md")):
        if any(part.startswith(".") for part in path.relative_to(VAULT_DIR).parts):
            continue
        paths.append(str(path.relative_to(VAULT_DIR)))
        if len(paths) >= limit:
            break
    return "\n".join(paths) or "пусто"


@mcp.tool()
def vault_create(path: str, content: str, title: str = "") -> str:
    """Создать заметку в AI/. Существующую не перезаписывает."""
    try:
        target = _safe_path(path, for_write=True)
    except ValueError as exc:
        return f"ошибка: {exc}"
    if target.exists():
        return f"файл уже существует: {path} (используй vault_append)"
    target.parent.mkdir(parents=True, exist_ok=True)
    header = f"# {title}\n\n" if title and not content.lstrip().startswith("#") else ""
    target.write_text(header + content.rstrip() + "\n", encoding="utf-8")
    rel = str(target.relative_to(VAULT_DIR))
    reindex_file(rel, target.read_text(encoding="utf-8", errors="replace"))
    return f"{rel}: {commit([rel], f'ai: создать {rel}')}"


@mcp.tool()
def vault_append(path: str, content: str, heading: str = "") -> str:
    """Дополнить заметку в AI/ (создаст, если нет). heading — подзаголовок."""
    try:
        target = _safe_path(path, for_write=True)
    except ValueError as exc:
        return f"ошибка: {exc}"
    target.parent.mkdir(parents=True, exist_ok=True)
    block = ""
    if heading:
        block += f"\n\n## {heading}\n"
    block += "\n" + content.rstrip() + "\n"
    with target.open("a", encoding="utf-8") as handle:
        handle.write(block)
    rel = str(target.relative_to(VAULT_DIR))
    reindex_file(rel, target.read_text(encoding="utf-8", errors="replace"))
    return f"{rel}: {commit([rel], f'ai: дополнить {rel}')}"


@mcp.tool()
def vault_replace(path: str, old: str, new: str) -> str:
    """Точная замена фрагмента в заметке AI/ (первое вхождение)."""
    try:
        target = _safe_path(path, for_write=True)
    except ValueError as exc:
        return f"ошибка: {exc}"
    if not target.is_file():
        return f"нет файла: {path}"
    text = target.read_text(encoding="utf-8")
    if old not in text:
        return "фрагмент не найден — ничего не изменено"
    target.write_text(text.replace(old, new, 1), encoding="utf-8")
    rel = str(target.relative_to(VAULT_DIR))
    reindex_file(rel, target.read_text(encoding="utf-8", errors="replace"))
    return f"{rel}: {commit([rel], f'ai: правка {rel}')}"


@mcp.tool()
def vault_sync() -> str:
    """Подтянуть изменения из git (pull --rebase) и отправить локальные коммиты."""
    if not (VAULT_DIR / ".git").is_dir():
        return "вольт не подключён"
    pull = git("pull", "--rebase", "--autostash", check=False)
    _push_pending.set()
    return json.dumps(
        {"pull": pull.stdout.strip() or pull.stderr.strip()[:300], "push": "запланирован"},
        ensure_ascii=False,
    )


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
