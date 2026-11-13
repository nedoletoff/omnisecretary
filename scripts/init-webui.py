#!/usr/bin/env python3
"""Первичная настройка Open WebUI без ручных кликов.

Делает идемпотентно:
  1. создаёт владельца (первый пользователь становится администратором);
  2. выпускает API-ключ владельца;
  3. прописывает провайдера LLM из .env (базовый URL и ключ);
  4. импортирует пресеты-«лица» из presets/*.md.

Пароль владельца и API-ключ сохраняются в ${DATA_ROOT}/secrets/
(права 600) и в чат не печатаются.

Использование:
    ./scripts/init-webui.sh            # email/пароль из .env или генерация
    ./scripts/init-webui.sh --skip-presets
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import string
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from import_presets import build_models  # noqa: E402  (файл import-presets.py)


def load_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def api(url: str, path: str, token: str | None = None, payload: dict | None = None,
        method: str | None = None) -> tuple[int, dict | list | str]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        url.rstrip("/") + path, data=data, headers=headers,
        method=method or ("POST" if data is not None else "GET"),
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
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


def gen_password(length: int = 20) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def read_secret(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    result = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            result[key.strip()] = value.strip()
    return result


def write_secret(path: Path, values: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    old_umask = os.umask(0o077)
    try:
        path.write_text(
            "\n".join(f"{k}={v}" for k, v in values.items()) + "\n",
            encoding="utf-8",
        )
    finally:
        os.umask(old_umask)
    path.chmod(0o600)


def main() -> int:
    parser = argparse.ArgumentParser(description="Первичная настройка Open WebUI")
    parser.add_argument("--url", default="", help="адрес Open WebUI")
    parser.add_argument("--skip-presets", action="store_true")
    args = parser.parse_args()

    env = load_dotenv(ROOT / ".env")
    url = args.url or env.get("WEBUI_URL", "http://127.0.0.1")
    data_root = Path(env.get("DATA_ROOT", "/srv/omnisecretary"))
    secrets_file = data_root / "secrets" / "webui-owner.txt"

    stored = read_secret(secrets_file)
    email = env.get("WEBUI_ADMIN_EMAIL") or stored.get("EMAIL") or "nedoletoff"
    password = env.get("WEBUI_ADMIN_PASSWORD") or stored.get("PASSWORD") or gen_password()
    name = env.get("WEBUI_ADMIN_NAME") or "Owner"

    token = ""
    status, body = api(url, "/api/v1/auths/signup",
                       payload={"email": email, "password": password, "name": name})
    if status == 200 and isinstance(body, dict):
        token = body.get("token", "")
        print(f"[ok] владелец создан: {email} (администратор)")
    else:
        status, body = api(url, "/api/v1/auths/signin",
                           payload={"email": email, "password": password})
        if status != 200 or not isinstance(body, dict) or not body.get("token"):
            detail = body.get("detail") if isinstance(body, dict) else body
            print(f"[fail] вход не удался: HTTP {status} {detail}")
            print("       Если пользователь уже создан вручную — задайте "
                  "WEBUI_ADMIN_EMAIL и WEBUI_ADMIN_PASSWORD в .env.")
            return 1
        token = body["token"]
        print(f"[ok] вход выполнен: {email}")

    write_secret(secrets_file, {"EMAIL": email, "PASSWORD": password})

    status, body = api(url, "/api/v1/auths/api_key", token=token)
    api_key = body.get("api_key", "") if isinstance(body, dict) else ""
    if not api_key:
        # Права пользователей: API-ключи нужны скриптам, шаринг — нет.
        # Чаты и память у каждого пользователя свои, общий доступ выключаем.
        status, perms = api(url, "/api/v1/users/default/permissions", token=token)
        if status == 200 and isinstance(perms, dict):
            features = perms.setdefault("features", {})
            features["api_keys"] = True
            features["channels"] = False
            perms.setdefault("chat", {})["share"] = False
            perms.setdefault("notes", {})["allow_sharing"] = False
            perms.setdefault("folders", {})["allow_sharing"] = False
            status, body = api(url, "/api/v1/users/default/permissions",
                               token=token, payload=perms)
            if status == 200:
                print("[ok] права пользователей: API-ключи включены, шаринг выключен")
        status, body = api(url, "/api/v1/auths/api_key", token=token, payload={})
        api_key = body.get("api_key", "") if isinstance(body, dict) else ""
    if api_key:
        key_file = data_root / "secrets" / "webui-api-key.txt"
        write_secret(key_file, {"WEBUI_API_KEY": api_key})
        print(f"[ok] API-ключ сохранён в {key_file}")
    else:
        print(f"[warn] не удалось выпустить API-ключ (HTTP {status})")

    base_url = env.get("OPENAI_API_BASE_URL", "").rstrip("/")
    api_secret = env.get("OPENAI_API_KEY", "")
    if base_url and api_secret and api_secret != "CHANGE_ME":
        status, body = api(url, "/openai/config/update", token=token, payload={
            "ENABLE_OPENAI_API": True,
            "OPENAI_API_BASE_URLS": [base_url],
            "OPENAI_API_KEYS": [api_secret],
            "OPENAI_API_CONFIGS": {},
        })
        print(f"[{'ok' if status == 200 else 'warn'}] провайдер LLM: {base_url} "
              f"(HTTP {status})")
    else:
        print("[warn] OPENAI_API_BASE_URL/OPENAI_API_KEY не заданы — провайдер не настроен")

    if args.skip_presets:
        return 0

    base_model = env.get("PRESET_BASE_MODEL", "").strip()
    if not base_model:
        status, models = api(url, "/api/models", token=token)
        ids = [m.get("id") for m in models.get("data", [])] if isinstance(models, dict) else []
        ids = [i for i in ids if i]
        if ids:
            base_model = ids[0]
            print(f"[info] базовая модель выбрана автоматически: {base_model}")

    models = build_models(base_model or None)
    status, body = api(url, "/api/v1/models/import", token=token,
                       payload={"models": models})
    if status == 200:
        print(f"[ok] пресеты импортированы: {len(models)}")
        for model in models:
            print(f"     - {model['id']}")
    else:
        print(f"[warn] импорт пресетов: HTTP {status} {body}")

    # В v0.13 модель без грантов приватна (видна только владельцу).
    # Открываем read всем пользователям: истории всё равно раздельные,
    # а пресеты — общий инструмент семьи.
    public_read = [{"principal_type": "user", "principal_id": "*", "permission": "read"}]
    for model_id, model_name in [(m["id"], m["name"]) for m in models] + (
        [(base_model, base_model)] if base_model else []
    ):
        status, body = api(url, "/api/v1/models/model/access/update", token=token,
                           payload={"id": model_id, "name": model_name,
                                    "access_grants": public_read})
        if status != 200:
            print(f"[warn] доступ к модели {model_id}: HTTP {status} {body}")
    print("[ok] доступ к моделям открыт всем пользователям (read)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
