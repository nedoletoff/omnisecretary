#!/usr/bin/env python3
"""Регистрирует инструменты OmniSecretary в Open WebUI и подключает их к моделям."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

BASE = os.environ.get("OWUI_URL", "http://127.0.0.1").rstrip("/")
KEY_FILE = Path(os.environ.get(
    "WEBUI_API_KEY_FILE", "/srv/omnisecretary/secrets/webui-api-key.txt"))
TOOLS_DIR = Path(__file__).resolve().parent / "webui-tools"
TOOLS = {
    "omni_services": "OmniSecretary: сервисы",
    "omni_research": "OmniSecretary: исследование",
}

KEY = KEY_FILE.read_text().split("=", 1)[-1].strip()
if not KEY:
    raise SystemExit("Пустой WEBUI_API_KEY")


def api(path: str, body: dict | None = None, method: str | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE + path, data=data,
        method=method or ("POST" if data is not None else "GET"),
        headers={"Authorization": "Bearer " + KEY,
                 "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            raw = response.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="replace")
        raise RuntimeError(f"HTTP {e.code} {path}: {raw[:300]}") from e


for tool_id, name in TOOLS.items():
    code = (TOOLS_DIR / f"{tool_id}.py").read_text()
    payload = {
        "id": tool_id,
        "name": name,
        "content": code,
        "meta": {"description": "Инструменты OmniSecretary"},
        "access_grants": [
            {"principal_type": "user", "principal_id": "*", "permission": "read"}
        ],
    }
    try:
        existing = api(f"/api/v1/tools/id/{tool_id}")
        if existing and existing.get("id"):
            api(f"/api/v1/tools/id/{tool_id}/update", payload)
            print(f"обновлён инструмент {tool_id}")
        else:
            api("/api/v1/tools/create", payload)
            print(f"создан инструмент {tool_id}")
    except Exception:
        try:
            api("/api/v1/tools/create", payload)
            print(f"создан инструмент {tool_id}")
        except Exception as exc:
            print(f"ОШИБКА {tool_id}: {exc}")

models = api("/api/models").get("data", [])
for info in models:
    model_id = info.get("id", "")
    if not model_id or "private" in model_id:
        continue
    try:
        model = api(f"/api/v1/models/model?id={model_id}")
    except Exception:
        continue
    meta = dict(model.get("meta") or {})
    ids = list(meta.get("toolIds") or [])
    changed = False
    for tool_id in TOOLS:
        if tool_id not in ids:
            ids.append(tool_id)
            changed = True
    if not changed:
        continue
    meta["toolIds"] = ids
    try:
        api("/api/v1/models/model/update", {
            "id": model.get("id"),
            "name": model.get("name") or model_id,
            "base_model_id": model.get("base_model_id"),
            "meta": meta,
            "params": dict(model.get("params") or {}),
            "is_active": model.get("is_active", True),
            "commit_message": "Подключены инструменты OmniSecretary",
        })
        print(f"модель {model_id}: инструменты подключены")
    except Exception as exc:
        print(f"ОШИБКА модели {model_id}: {exc}")
