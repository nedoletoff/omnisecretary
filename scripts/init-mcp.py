#!/usr/bin/env python3
"""Регистрация MCP-серверов и выдача инструментов пресетам.

Идемпотентно: список tool server connections перезаписывается нашими
записями, access_grants остаются пустыми → серверы доступны только
администратору (владельцу). Пресеты получают ссылки server:mcp:<id>.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

DATA_ROOT = Path(os.environ.get("DATA_ROOT", "/srv/omnisecretary"))
URL = os.environ.get("WEBUI_URL", "http://127.0.0.1").rstrip("/")

SERVERS = [
    {
        "id": "vault",
        "name": "Obsidian",
        "url": "http://mcp-vault:8000/mcp",
        "description": "Вольт Obsidian: поиск, чтение, запись в AI/ с git-коммитами",
    },
    {
        "id": "files",
        "name": "Files",
        "url": "http://mcp-files:8000/mcp",
        "description": "Общий каталог файлов на Pi",
    },
    {
        "id": "github",
        "name": "GitHub",
        "url": "http://mcp-github:8000/mcp",
        "description": "GitHub: файлы, коммиты, issues, pull requests",
    },
    {
        "id": "vikunja",
        "name": "Vikunja",
        "url": "http://mcp-vikunja:8000/mcp",
        "description": "Задачи Vikunja на естественном языке",
    },
    {
        "id": "sandbox",
        "name": "Sandbox",
        "url": "http://mcp-sandbox:8000/mcp",
        "description": "Песочница: код и прототипы, без доступа к хосту",
    },
]

# Какой пресет какие серверы видит: экономия контекста и принцип минимальных прав.
PRESET_TOOLS = {
    "secretary": ["vikunja", "files"],
    "omni": ["vikunja", "files", "vault"],
    "archivist": ["vault"],
    "coder": ["github", "sandbox", "files"],
    "private": [],
}


def secret(path: Path, key: str) -> str:
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith(key + "="):
                return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return ""


def api(path: str, token: str, payload: dict | None = None, method: str | None = None):
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}
    request = urllib.request.Request(
        URL + path, data=data, headers=headers,
        method=method or ("POST" if data is not None else "GET"),
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            body = response.read().decode("utf-8", "replace")
            try:
                return response.status, json.loads(body)
            except json.JSONDecodeError:
                return response.status, body
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")[:400]


def main() -> int:
    token = os.environ.get("WEBUI_API_KEY", "").strip()
    if not token:
        token = secret(DATA_ROOT / "secrets" / "webui-api-key.txt", "WEBUI_API_KEY")
    if not token:
        print("[fail] нет API-ключа владельца (make init-webui)")
        return 1

    connections = [
        {
            "url": server["url"],
            "path": "",
            "type": "mcp",
            "auth_type": "none",
            "forward_cookies": False,
            "headers": {},
            "key": "",
            "config": {"enable": True, "access_grants": []},
            "info": {"id": server["id"], "name": server["name"],
                     "description": server["description"]},
        }
        for server in SERVERS
    ]
    status, body = api("/api/v1/configs/tool_servers", token,
                       {"TOOL_SERVER_CONNECTIONS": connections})
    if status != 200:
        print(f"[fail] регистрация MCP: HTTP {status} {body}")
        return 1
    print(f"[ok] MCP-серверы зарегистрированы: {len(connections)} (доступ только владельцу)")

    for server in SERVERS:
        status, body = api("/api/v1/configs/tool_servers/verify", token, {
            "url": server["url"], "path": "", "type": "mcp", "auth_type": "none",
            "headers": {}, "key": "", "config": {}, "info": {"id": server["id"]},
        })
        if status == 200:
            print(f"     - {server['id']}: связь есть")
        else:
            print(f"     - {server['id']}: [warn] HTTP {status} {str(body)[:120]}")

    status, body = api("/api/v1/models/list", token)
    models = body.get("items", []) if isinstance(body, dict) else []
    if status != 200 or not models:
        print(f"[warn] не удалось получить пресеты: HTTP {status} {str(body)[:200]}")
        return 0
    by_id = {model.get("id"): model for model in models}
    for preset, servers in PRESET_TOOLS.items():
        model = by_id.get(preset)
        if not model:
            print(f"[warn] пресет {preset} не найден")
            continue
        meta = dict(model.get("meta") or {})
        meta["toolIds"] = [f"server:mcp:{server}" for server in servers]
        payload = {
            "id": model["id"],
            "name": model.get("name", preset),
            "meta": meta,
            "params": model.get("params") or {},
            "access_grants": model.get("access_control") or [],
        }
        status, body = api("/api/v1/models/model/update", token, payload)
        if status == 200:
            tools = ", ".join(servers) or "нет"
            print(f"[ok] {preset}: инструменты → {tools}")
        else:
            print(f"[warn] {preset}: HTTP {status} {str(body)[:200]}")

    print("\nИнструменты доступны только владельцу: серверы без access_grants")
    print("видны лишь администратору, а MCP-контейнеры не публикуют порты.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
