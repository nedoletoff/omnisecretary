"""Per-user Obsidian vaults (git + FTS5 index).

Каждый пользователь подключает свой приватный GitHub-репозиторий и PAT.
Данные изолированы: /vaults/<uid>/repo и /vaults/<uid>/index.db.
Токен хранится в portal.db в зашифрованном виде (AES-256-GCM).
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import threading
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

VAULTS_ROOT = Path(os.environ.get("VAULTS_ROOT", "/vaults"))
GIT_TIMEOUT = int(os.environ.get("VAULT_GIT_TIMEOUT", "120"))
WRITE_PREFIX = os.environ.get("VAULT_WRITE_PREFIX", "AI").strip("/") or "AI"
PUSH_ENABLED = os.environ.get("VAULT_PUSH_ENABLED", "true").lower() in ("1", "true", "yes", "on")

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock(uid: str) -> threading.Lock:
    with _locks_guard:
        if uid not in _locks:
            _locks[uid] = threading.Lock()
        return _locks[uid]


def _safe_uid(uid: str) -> str:
    """Стабильное безопасное имя каталога для пользователя."""
    clean = re.sub(r"[^a-zA-Z0-9._-]+", "_", uid).strip("_")[:40] or "user"
    suffix = hashlib.sha256(uid.encode("utf-8")).hexdigest()[:12]
    return f"{clean}_{suffix}"


def _user_root(uid: str) -> Path:
    return VAULTS_ROOT / _safe_uid(uid)


def repo_dir(uid: str) -> Path:
    return _user_root(uid) / "repo"


def index_path(uid: str) -> Path:
    return _user_root(uid) / "index.db"


# ── Шифрование токена ───────────────────────────────────────────────────────

def _key(data_dir: Path) -> bytes:
    key_file = data_dir / "token.key"
    if key_file.exists():
        raw = key_file.read_bytes()
        return raw[:32] if len(raw) >= 32 else raw.ljust(32, b"\0")
    key = os.urandom(32)
    old = os.umask(0o077)
    try:
        key_file.write_bytes(key)
    finally:
        os.umask(old)
    try:
        key_file.chmod(0o600)
    except OSError:
        pass
    return key


def encrypt_token(data_dir: Path, token: str) -> str:
    if not token:
        return ""
    nonce = os.urandom(12)
    blob = AESGCM(_key(data_dir)).encrypt(nonce, token.encode("utf-8"), b"vault")
    return base64.b64encode(nonce + blob).decode("ascii")


def decrypt_token(data_dir: Path, value: str) -> str:
    if not value:
        return ""
    try:
        raw = base64.b64decode(value)
        nonce, blob = raw[:12], raw[12:]
        return AESGCM(_key(data_dir)).decrypt(nonce, blob, b"vault").decode("utf-8")
    except Exception:
        return ""


# ── БД конфигурации ─────────────────────────────────────────────────────────

DEFAULT_PERMISSIONS = [
    {"path": "", "read": True, "list": True, "search": True,
     "create": False, "append": False, "modify": False, "delete": False},
    {"path": "AI/", "read": True, "list": True, "search": True,
     "create": True, "append": True, "modify": True, "delete": False},
]


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS user_vaults (
            owui_id TEXT PRIMARY KEY,
            repo_url TEXT NOT NULL DEFAULT '',
            branch TEXT NOT NULL DEFAULT 'main',
            encrypted_token TEXT NOT NULL DEFAULT '',
            write_prefix TEXT NOT NULL DEFAULT 'AI',
            permissions_json TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'not_configured',
            last_error TEXT NOT NULL DEFAULT '',
            last_sync TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT ''
        );
        """
    )
    # миграция: добавить колонку, если таблица старая
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(user_vaults)")]
        if "permissions_json" not in cols:
            conn.execute("ALTER TABLE user_vaults ADD COLUMN permissions_json TEXT NOT NULL DEFAULT ''")
    except Exception:
        pass
    conn.commit()


def get_config(conn: sqlite3.Connection, uid: str) -> dict | None:
    ensure_table(conn)
    row = conn.execute("SELECT * FROM user_vaults WHERE owui_id=?", (uid,)).fetchone()
    if not row:
        return None
    return dict(row)


def get_permissions(conn: sqlite3.Connection, uid: str) -> list[dict]:
    cfg = get_config(conn, uid)
    raw = (cfg or {}).get("permissions_json") or ""
    if raw:
        try:
            perms = json.loads(raw)
            if isinstance(perms, list) and perms:
                return perms
        except Exception:
            pass
    return [dict(p) for p in DEFAULT_PERMISSIONS]


def set_permissions(conn: sqlite3.Connection, uid: str, permissions: list[dict]) -> None:
    ensure_table(conn)
    now = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    conn.execute(
        "UPDATE user_vaults SET permissions_json=?, updated_at=? WHERE owui_id=?",
        (json.dumps(permissions, ensure_ascii=False), now, uid),
    )
    conn.commit()


def normalize_permissions(perms: object) -> list[dict]:
    """Валидация и нормализация правил доступа."""
    if not isinstance(perms, list):
        raise ValueError("permissions должен быть списком")
    out = []
    seen = set()
    allowed_actions = ("read", "list", "search", "create", "append", "modify", "delete")
    for item in perms:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path", "")).strip().lstrip("/")
        if ".." in Path(path).parts:
            raise ValueError(f"недопустимый путь в правиле: {path}")
        # нормализуем: папки заканчиваются на /
        if path and not path.endswith("/") and "." not in Path(path).name:
            # может быть и файл, и папка; оставляем как есть, но для префиксов лучше /
            pass
        key = path
        if key in seen:
            continue
        seen.add(key)
        rule = {"path": path}
        for a in allowed_actions:
            rule[a] = bool(item.get(a, False))
        out.append(rule)
    if not out:
        raise ValueError("нужен хотя бы один набор правил")
    # гарантируем правило по умолчанию (корень), иначе неявный запрет всего
    return out


def check_permission(conn: sqlite3.Connection, uid: str, rel_path: str, action: str) -> bool:
    """Проверка права на действие с путём. Наиболее специфичное правило побеждает."""
    rel_path = (rel_path or "").strip().lstrip("/")
    # нормализация: запрет выхода за пределы
    if ".." in Path(rel_path).parts:
        return False
    perms = get_permissions(conn, uid)
    best = None
    best_len = -1
    for rule in perms:
        prefix = str(rule.get("path", "")).strip().lstrip("/")
        # совпадение: префикс "" совпадает со всем; иначе путь == prefix или начинается с prefix/
        if prefix == "":
            match = True
        elif rel_path == prefix.rstrip("/"):
            match = True
        elif rel_path.startswith(prefix if prefix.endswith("/") else prefix + "/"):
            match = True
        elif prefix.endswith("/") and rel_path.startswith(prefix):
            match = True
        else:
            match = False
        if not match:
            continue
        if len(prefix) > best_len:
            best_len = len(prefix)
            best = rule
    if not best:
        return False
    return bool(best.get(action, False))


def upsert_config(conn: sqlite3.Connection, uid: str, repo_url: str, branch: str,
                  encrypted_token: str, write_prefix: str = "AI",
                  permissions: list[dict] | None = None) -> None:
    ensure_table(conn)
    now = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    perms_json = _json.dumps(permissions if permissions is not None else DEFAULT_PERMISSIONS,
                             ensure_ascii=False)
    conn.execute(
        """
        INSERT INTO user_vaults (owui_id, repo_url, branch, encrypted_token, write_prefix,
                                 permissions_json, status, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, 'configured', ?)
        ON CONFLICT(owui_id) DO UPDATE SET
            repo_url=excluded.repo_url,
            branch=excluded.branch,
            encrypted_token=CASE WHEN excluded.encrypted_token != '' THEN excluded.encrypted_token ELSE user_vaults.encrypted_token END,
            write_prefix=excluded.write_prefix,
            permissions_json=CASE WHEN excluded.permissions_json != '' THEN excluded.permissions_json ELSE user_vaults.permissions_json END,
            status='configured',
            updated_at=excluded.updated_at
        """,
        (uid, repo_url, branch, encrypted_token, write_prefix, perms_json, now),
    )
    conn.commit()


def set_state(conn: sqlite3.Connection, uid: str, status: str = "", error: str = "", synced: bool = False) -> None:
    ensure_table(conn)
    now = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    row = get_config(conn, uid)
    if not row:
        return
    fields = ["updated_at=?"]
    vals: list = [now]
    if status:
        fields.append("status=?"); vals.append(status)
    if error is not None:
        fields.append("last_error=?"); vals.append(error[:500])
    if synced:
        fields.append("last_sync=?"); vals.append(now)
    vals.append(uid)
    conn.execute(f"UPDATE user_vaults SET {', '.join(fields)} WHERE owui_id=?", vals)
    conn.commit()


def delete_config(conn: sqlite3.Connection, uid: str) -> None:
    ensure_table(conn)
    conn.execute("DELETE FROM user_vaults WHERE owui_id=?", (uid,))
    conn.commit()


# ── Git ─────────────────────────────────────────────────────────────────────

def _clean_repo_url(raw: str) -> str:
    raw = (raw or "").strip()
    parts = urlsplit(raw)
    if parts.scheme != "https" or not parts.hostname:
        raise ValueError("нужен https-URL репозитория")
    if parts.username or parts.password or "@" in parts.netloc:
        raise ValueError("URL не должен содержать логин или токен")
    return raw


def _git_env(token: str) -> dict[str, str]:
    env = dict(os.environ)
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    if token:
        basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        env["GIT_CONFIG_COUNT"] = "1"
        env["GIT_CONFIG_KEY_0"] = "http.extraheader"
        env["GIT_CONFIG_VALUE_0"] = f"AUTHORIZATION: basic {basic}"
    return env


def _git(repo: Path, token: str, *args: str, check: bool = True, timeout: int = GIT_TIMEOUT) -> subprocess.CompletedProcess:
    cmd = [
        "git", "-C", str(repo),
        "-c", f"safe.directory={repo}",
        "-c", "user.name=OmniSecretary",
        "-c", "user.email=ai@omnisecretary.local",
        *args,
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=_git_env(token))
    if check and res.returncode != 0:
        msg = (res.stderr or res.stdout or "git error").strip()[:400]
        raise RuntimeError(msg)
    return res


def is_ready(uid: str) -> bool:
    return (repo_dir(uid) / ".git").is_dir()


def clone_or_update(uid: str, repo_url: str, branch: str, token: str) -> None:
    root = _user_root(uid)
    repo = repo_dir(uid)
    root.mkdir(parents=True, exist_ok=True)
    with _lock(uid):
        if (repo / ".git").is_dir():
            _git(repo, token, "remote", "set-url", "origin", repo_url)
            fetch = _git(repo, token, "fetch", "origin", branch, check=False, timeout=300)
            if fetch.returncode != 0:
                raise RuntimeError((fetch.stderr or fetch.stdout).strip()[:400] or "fetch failed")
            _git(repo, token, "checkout", branch, check=False)
            _git(repo, token, "pull", "--rebase", "--autostash", "origin", branch, check=False)
        else:
            if repo.exists() and any(repo.iterdir()):
                raise RuntimeError("каталог вольта не пуст и не является git-репозиторием")
            repo.mkdir(parents=True, exist_ok=True)
            # clone into target dir
            env = _git_env(token)
            env.setdefault("GIT_TERMINAL_PROMPT", "0")
            res = subprocess.run(
                ["git", "clone", "--branch", branch, repo_url, str(repo)],
                capture_output=True, text=True, timeout=600, env=env,
            )
            if res.returncode != 0:
                msg = (res.stderr or res.stdout or "clone failed").strip()[:400]
                raise RuntimeError(msg)
        ensure_index(uid)


def sync(uid: str, token: str) -> dict:
    repo = repo_dir(uid)
    if not (repo / ".git").is_dir():
        raise RuntimeError("вольт не подключён")
    with _lock(uid):
        pull = _git(repo, token, "pull", "--rebase", "--autostash", check=False)
        pushed = False
        push_msg = ""
        if PUSH_ENABLED:
            push = _git(repo, token, "push", check=False)
            pushed = push.returncode == 0
            push_msg = (push.stdout or push.stderr).strip()[:300]
            if not pushed:
                # попытка rebase+push при расхождении
                _git(repo, token, "pull", "--rebase", "--autostash", check=False)
                push = _git(repo, token, "push", check=False)
                pushed = push.returncode == 0
                push_msg = (push.stdout or push.stderr).strip()[:300]
        ensure_index(uid)
        return {
            "pull": (pull.stdout or pull.stderr).strip()[:300],
            "pushed": pushed,
            "push": push_msg,
        }


def status(uid: str, token: str) -> dict:
    repo = repo_dir(uid)
    if not (repo / ".git").is_dir():
        return {"ready": False, "reason": "вольт не подключён"}
    branch = _git(repo, token, "rev-parse", "--abbrev-ref", "HEAD", check=False).stdout.strip()
    remote = _git(repo, token, "remote", "get-url", "origin", check=False).stdout.strip()
    # не светим токен, если он вдруг в remote URL
    try:
        p = urlsplit(remote)
        if p.username or p.password:
            remote = f"{p.scheme}://{p.hostname}{p.path}"
    except Exception:
        pass
    dirty = _git(repo, token, "status", "--porcelain", check=False).stdout.strip().splitlines()
    log = _git(repo, token, "log", "--oneline", "-5", check=False).stdout.strip().splitlines()
    ahead = _git(repo, token, "rev-list", "--count", "@{u}..HEAD", check=False).stdout.strip()
    behind = _git(repo, token, "rev-list", "--count", "HEAD..@{u}", check=False).stdout.strip()
    return {
        "ready": True,
        "branch": branch,
        "remote": remote,
        "dirty_files": len(dirty),
        "ahead": ahead,
        "behind": behind,
        "last_commits": log,
        "write_prefix": WRITE_PREFIX + "/",
    }


# ── Индекс и поиск ──────────────────────────────────────────────────────────

def _index_conn(uid: str) -> sqlite3.Connection:
    ip = index_path(uid)
    ip.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(ip, timeout=10)
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


def ensure_index(uid: str) -> int:
    repo = repo_dir(uid)
    if not repo.is_dir():
        return 0
    conn = _index_conn(uid)
    known = {r[0]: (r[1], r[2]) for r in conn.execute("SELECT path, mtime, size FROM notes")}
    seen: set[str] = set()
    updated = 0
    for path in repo.rglob("*.md"):
        try:
            rel_parts = path.relative_to(repo).parts
        except ValueError:
            continue
        if any(p.startswith(".") for p in rel_parts):
            continue
        rel = str(path.relative_to(repo))
        try:
            st = path.stat()
        except OSError:
            continue
        seen.add(rel)
        if known.get(rel) == (st.st_mtime, st.st_size):
            continue
        try:
            body = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        conn.execute("DELETE FROM notes_fts WHERE path = ?", (rel,))
        conn.execute("INSERT INTO notes_fts (path, body) VALUES (?, ?)", (rel, body))
        conn.execute(
            "INSERT INTO notes (path, mtime, size) VALUES (?, ?, ?) "
            "ON CONFLICT(path) DO UPDATE SET mtime=excluded.mtime, size=excluded.size",
            (rel, st.st_mtime, st.st_size),
        )
        updated += 1
    for rel in set(known) - seen:
        conn.execute("DELETE FROM notes_fts WHERE path = ?", (rel,))
        conn.execute("DELETE FROM notes WHERE path = ?", (rel,))
    conn.commit()
    conn.close()
    return updated


def reindex_file(uid: str, rel: str, body: str) -> None:
    conn = _index_conn(uid)
    conn.execute("DELETE FROM notes_fts WHERE path = ?", (rel,))
    conn.execute("INSERT INTO notes_fts (path, body) VALUES (?, ?)", (rel, body))
    conn.commit()
    conn.close()


def _safe_rel(relative: str) -> str:
    relative = (relative or "").strip().lstrip("/")
    if not relative:
        raise ValueError("пустой путь")
    if ".." in Path(relative).parts:
        raise ValueError("недопустимый путь")
    return relative


def _resolve_path(uid: str, relative: str) -> tuple[Path, str]:
    rel = _safe_rel(relative)
    repo = repo_dir(uid).resolve()
    target = (repo / rel).resolve()
    if repo != target and repo not in target.parents:
        raise ValueError("путь вне вольта")
    if target.suffix.lower() not in (".md", ".markdown", ""):
        # для list/search передаётся папка; для файлов требуем .md
        pass
    rel_norm = str(target.relative_to(repo))
    return target, rel_norm


def _ensure_md(rel: str) -> None:
    if Path(rel).suffix.lower() not in (".md", ".markdown"):
        raise ValueError("только markdown-заметки (.md)")


def list_files(uid: str, conn: sqlite3.Connection, q: str = "", limit: int = 50) -> list[dict]:
    repo = repo_dir(uid)
    if not repo.is_dir():
        return []
    q = (q or "").strip().lower()
    items = []
    for path in sorted(repo.rglob("*.md")):
        try:
            rel = str(path.relative_to(repo))
        except ValueError:
            continue
        if any(p.startswith(".") for p in Path(rel).parts):
            continue
        if q and q not in rel.lower():
            continue
        if not check_permission(conn, uid, rel, "list") and not check_permission(conn, uid, rel, "read"):
            continue
        try:
            st = path.stat()
            items.append({"path": rel, "size": st.st_size,
                          "mtime": datetime.utcfromtimestamp(st.st_mtime).isoformat(timespec="seconds") + "Z"})
        except OSError:
            continue
        if len(items) >= max(1, min(int(limit), 200)):
            break
    return items


def read_note(uid: str, conn: sqlite3.Connection, path: str) -> dict:
    target, rel = _resolve_path(uid, path)
    _ensure_md(rel)
    if not check_permission(conn, uid, rel, "read"):
        raise PermissionError("нет права на чтение этого пути")
    if not target.is_file():
        raise FileNotFoundError("файл не найден")
    text = target.read_text(encoding="utf-8", errors="replace")
    return {"path": rel, "content": text}


def search(uid: str, conn: sqlite3.Connection, query: str, limit: int = 20) -> list[dict]:
    if not is_ready(uid):
        return []
    ensure_index(uid)
    limit = max(1, min(int(limit), 50))
    idx_conn = _index_conn(uid)
    try:
        rows = idx_conn.execute(
            "SELECT path, snippet(notes_fts, 1, '«', '»', '…', 16) AS snip, rank "
            "FROM notes_fts WHERE notes_fts MATCH ? ORDER BY rank LIMIT ?",
            (query, limit * 3),  # берём с запасом, часть отфильтруем по правам
        ).fetchall()
    except sqlite3.OperationalError:
        rows = []
    finally:
        idx_conn.close()
    results = []
    for p, s, _ in rows:
        if check_permission(conn, uid, p, "read") or check_permission(conn, uid, p, "search"):
            results.append({"path": p, "snippet": s})
        if len(results) >= limit:
            break
    return results


def create_note(uid: str, conn: sqlite3.Connection, token: str,
                folder: str, title: str, text: str) -> dict:
    repo = repo_dir(uid)
    if not (repo / ".git").is_dir():
        raise RuntimeError("вольт не подключён")
    folder = _safe_rel((folder or "AI/Inbox").strip().strip("/"))
    stamp = datetime.now().strftime("%Y-%m-%d-%H%M")
    slug = re.sub(r"[^\w\-]+", "-", (title or "").lower(), flags=re.UNICODE).strip("-")[:40]
    name = f"{stamp}-{slug}.md" if slug else f"{stamp}.md"
    rel = (folder.rstrip("/") + "/" + name).lstrip("/")
    _ensure_md(rel)
    if not check_permission(conn, uid, rel, "create"):
        raise PermissionError(f"нет права на создание в «{folder}/»")
    target, rel = _resolve_path(uid, rel)
    target.parent.mkdir(parents=True, exist_ok=True)
    body = f"# {title}\n\n{text}\n" if title else text.rstrip() + "\n"
    target.write_text(body, encoding="utf-8")
    reindex_file(uid, rel, body)
    with _lock(uid):
        _git(repo, token, "add", rel)
        commit = _git(repo, token, "commit", "-m", f"note: {title or name} (клиент)", check=False)
        if commit.returncode != 0:
            raise RuntimeError((commit.stderr or commit.stdout).strip()[:300] or "commit failed")
        sha = _git(repo, token, "rev-parse", "--short", "HEAD", check=False).stdout.strip()
        pushed = False
        if PUSH_ENABLED:
            _git(repo, token, "pull", "--rebase", "--autostash", check=False)
            push = _git(repo, token, "push", check=False)
            pushed = push.returncode == 0
    return {"path": rel, "commit": sha, "pushed": pushed}


def update_note(uid: str, conn: sqlite3.Connection, token: str,
                path: str, content: str) -> dict:
    repo = repo_dir(uid)
    if not (repo / ".git").is_dir():
        raise RuntimeError("вольт не подключён")
    target, rel = _resolve_path(uid, path)
    _ensure_md(rel)
    if not check_permission(conn, uid, rel, "modify"):
        raise PermissionError("нет права на изменение этого файла")
    if not target.is_file():
        raise FileNotFoundError("файл не найден")
    target.write_text(content if content.endswith("\n") else content + "\n", encoding="utf-8")
    reindex_file(uid, rel, target.read_text(encoding="utf-8", errors="replace"))
    with _lock(uid):
        _git(repo, token, "add", rel)
        status = _git(repo, token, "status", "--porcelain", "--", rel, check=False).stdout.strip()
        if not status:
            return {"path": rel, "commit": "", "pushed": False, "changed": False}
        commit = _git(repo, token, "commit", "-m", f"note: изменить {rel}", check=False)
        if commit.returncode != 0:
            raise RuntimeError((commit.stderr or commit.stdout).strip()[:300] or "commit failed")
        sha = _git(repo, token, "rev-parse", "--short", "HEAD", check=False).stdout.strip()
        pushed = False
        if PUSH_ENABLED:
            _git(repo, token, "pull", "--rebase", "--autostash", check=False)
            push = _git(repo, token, "push", check=False)
            pushed = push.returncode == 0
    return {"path": rel, "commit": sha, "pushed": pushed, "changed": True}


def append_note(uid: str, conn: sqlite3.Connection, token: str,
                path: str, content: str, heading: str = "") -> dict:
    repo = repo_dir(uid)
    if not (repo / ".git").is_dir():
        raise RuntimeError("вольт не подключён")
    target, rel = _resolve_path(uid, path)
    _ensure_md(rel)
    if not check_permission(conn, uid, rel, "append") and not check_permission(conn, uid, rel, "modify"):
        raise PermissionError("нет права на дополнение этого файла")
    target.parent.mkdir(parents=True, exist_ok=True)
    block = ""
    if heading:
        block += f"\n\n## {heading}\n"
    block += "\n" + content.rstrip() + "\n"
    if target.exists():
        with target.open("a", encoding="utf-8") as f:
            f.write(block)
    else:
        # append в несуществующий файл = создание, требует create
        if not check_permission(conn, uid, rel, "create"):
            raise PermissionError("нет права на создание этого файла")
        target.write_text(block.lstrip("\n"), encoding="utf-8")
    reindex_file(uid, rel, target.read_text(encoding="utf-8", errors="replace"))
    with _lock(uid):
        _git(repo, token, "add", rel)
        commit = _git(repo, token, "commit", "-m", f"note: дополнить {rel}", check=False)
        if commit.returncode != 0:
            raise RuntimeError((commit.stderr or commit.stdout).strip()[:300] or "commit failed")
        sha = _git(repo, token, "rev-parse", "--short", "HEAD", check=False).stdout.strip()
        pushed = False
        if PUSH_ENABLED:
            _git(repo, token, "pull", "--rebase", "--autostash", check=False)
            push = _git(repo, token, "push", check=False)
            pushed = push.returncode == 0
    return {"path": rel, "commit": sha, "pushed": pushed}


def remove_local(uid: str) -> None:
    """Удаляет локальный клон и индекс пользователя."""
    import shutil
    root = _user_root(uid)
    if root.exists():
        shutil.rmtree(root, ignore_errors=True)
