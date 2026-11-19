"""Конфигурация провайдеров LLM для веб-чата.

Портал хранит настройки в portal.db (ключи — AES-256-GCM), а для шлюза
пишет компактный llm-config.json с расшифрованными ключами (права 600).
Шлюз монтирует этот файл только для чтения и выбирает конфиг по ID
пользователя, который Open WebUI передаёт в подписанном JWT-заголовке.

Почему так: Open WebUI не умеет хранить персональные OpenAI-ключи для
пользователей, а пробрасывать ключ в браузер небезопасно. Шлюз — единственная
точка, которая видит ключ и делает запрос к провайдеру от имени сервера.
"""

from __future__ import annotations

import base64
import json
import os
import sqlite3
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

DATA_DIR = Path(os.environ.get("PORTAL_DATA", "/data"))
DB_PATH = DATA_DIR / "portal.db"
TOKEN_KEY_FILE = DATA_DIR / "token.key"
CONFIG_PATH = Path(os.environ.get("LLM_CONFIG_PATH", str(DATA_DIR / "llm-config.json")))
SYSTEM_KEY = "__system__"

_key_cache: bytes | None = None


def _key() -> bytes:
    """Тот же ключ, что и у GitHub-токенов, но с другим контекстом шифрования."""
    global _key_cache
    if _key_cache is None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        if TOKEN_KEY_FILE.exists():
            raw = TOKEN_KEY_FILE.read_bytes()
            _key_cache = raw[:32] if len(raw) >= 32 else raw.ljust(32, b"\0")
        else:
            key = os.urandom(32)
            old = os.umask(0o077)
            try:
                TOKEN_KEY_FILE.write_bytes(key)
            finally:
                os.umask(old)
            TOKEN_KEY_FILE.chmod(0o600)
            _key_cache = key
    return _key_cache


def enc(value: str) -> str:
    value = (value or "").strip()
    if not value or value.startswith("enc:llm:v1:"):
        return value
    nonce = os.urandom(12)
    blob = AESGCM(_key()).encrypt(nonce, value.encode(), b"llm")
    return "enc:llm:v1:" + base64.urlsafe_b64encode(nonce).decode() + \
        ":" + base64.urlsafe_b64encode(blob).decode()


def dec(value: str) -> str:
    if not value or not value.startswith("enc:llm:v1:"):
        return value
    try:
        _, _, nonce_b64, blob_b64 = value.split(":", 3)
        return AESGCM(_key()).decrypt(
            base64.urlsafe_b64decode(nonce_b64),
            base64.urlsafe_b64decode(blob_b64), b"llm").decode()
    except Exception:  # noqa: BLE001 — битая запись не должна ронять портал
        return ""


def db() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS llm_providers (
               owner_key TEXT PRIMARY KEY,
               name TEXT NOT NULL DEFAULT '',
               base_url TEXT NOT NULL DEFAULT '',
               api_key TEXT NOT NULL DEFAULT '',
               model TEXT NOT NULL DEFAULT '',
               enabled INTEGER NOT NULL DEFAULT 0,
               fallback INTEGER NOT NULL DEFAULT 1,
               updated_at TEXT NOT NULL
           )"""
    )
    return conn


def get(owner_key: str) -> dict:
    conn = db()
    row = conn.execute("SELECT * FROM llm_providers WHERE owner_key = ?",
                       (owner_key,)).fetchone()
    conn.close()
    return dict(row) if row else {}


def public(owner_key: str) -> dict:
    row = get(owner_key)
    if not row:
        return {"enabled": False, "name": "", "base_url": "", "model": "",
                "fallback": True, "has_key": False}
    return {
        "enabled": bool(row.get("enabled")),
        "name": row.get("name", ""),
        "base_url": row.get("base_url", ""),
        "model": row.get("model", ""),
        "fallback": bool(row.get("fallback", True)),
        "has_key": bool(row.get("api_key")),
    }


def save(owner_key: str, *, name: str, base_url: str, api_key: str, model: str,
         enabled: bool, fallback: bool, updated_at: str) -> None:
    old = get(owner_key)
    key_value = enc(api_key) if api_key.strip() else old.get("api_key", "")
    conn = db()
    conn.execute(
        """INSERT INTO llm_providers
               (owner_key, name, base_url, api_key, model, enabled, fallback, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(owner_key) DO UPDATE SET
               name = excluded.name,
               base_url = excluded.base_url,
               api_key = excluded.api_key,
               model = excluded.model,
               enabled = excluded.enabled,
               fallback = excluded.fallback,
               updated_at = excluded.updated_at""",
        (owner_key, name.strip(), base_url.strip().rstrip("/"), key_value,
         model.strip(), int(bool(enabled)), int(bool(fallback)), updated_at))
    conn.commit()
    conn.close()
    write_gateway_config()


def delete(owner_key: str) -> None:
    conn = db()
    conn.execute("DELETE FROM llm_providers WHERE owner_key = ?", (owner_key,))
    conn.commit()
    conn.close()
    write_gateway_config()


def _gateway_entry(row: dict | None) -> dict:
    if not row or not row.get("enabled"):
        return {"enabled": False}
    return {
        "enabled": True,
        "name": row.get("name", ""),
        "base_url": row.get("base_url", ""),
        "api_key": dec(row.get("api_key", "")),
        "model": row.get("model", ""),
        "fallback": bool(row.get("fallback", True)),
    }


def write_gateway_config() -> None:
    """Атомарно обновляет файл для шлюза. Ключи в файле — только на диске 600."""
    conn = db()
    rows = conn.execute("SELECT * FROM llm_providers").fetchall()
    conn.close()
    payload = {
        "system": _gateway_entry(get(SYSTEM_KEY)),
        "users": {},
    }
    for row in rows:
        owner = row["owner_key"]
        if owner == SYSTEM_KEY:
            continue
        payload["users"][owner] = _gateway_entry(dict(row))

    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_PATH.with_suffix(".json.tmp")
    old = os.umask(0o077)
    try:
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, CONFIG_PATH)
        CONFIG_PATH.chmod(0o600)
    finally:
        os.umask(old)
