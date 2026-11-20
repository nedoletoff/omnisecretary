#!/usr/bin/env python3
"""Регистрирует инструменты и режимы OmniSecretary в Open WebUI."""

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SECRETS = Path(os.environ.get("DATA_ROOT", "/srv/omnisecretary")) / "secrets"
OWUI = os.environ.get("OWUI_INTERNAL_URL", "http://127.0.0.1:8080")


def owner():
    path = SECRETS / "webui-owner.txt"
    data = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            data[key.strip()] = value.strip()
    return (data.get("LOGIN") or data.get("EMAIL") or data.get("USERNAME") or "nedoletoff",
            data.get("PASSWORD", ""))


def call(path, method="GET", body=None, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(
        OWUI + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers=headers,
        method=method)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=60) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            detail = json.loads(raw)
        except Exception:
            detail = raw.decode("utf-8", "replace")[:300]
        return exc.code, detail


def token():
    login, password = owner()
    status, data = call("/api/v1/auths/signin", "POST",
                        {"email": login, "password": password})
    if status != 200 or "token" not in data:
        raise SystemExit(f"не удалось войти в Open WebUI: {status}")
    return data["token"]


def sync_tool(t, tool_id, name, description, path):
    content = (ROOT / path).read_text(encoding="utf-8")
    payload = {
        "id": tool_id,
        "name": name,
        "content": content,
        "meta": {"description": description},
    }
    status, existing = call(f"/api/v1/tools/id/{tool_id}", token=t)
    if status == 200 and existing:
        endpoint, method = f"/api/v1/tools/id/{tool_id}/update", "POST"
    else:
        endpoint, method = "/api/v1/tools/create", "POST"
    status, result = call(endpoint, method, payload, t)
    print(f"инструмент {tool_id}: {status}")
    if status >= 400:
        print(str(result)[:500])
    return tool_id


MODEL_IDS = [
    "omnisecretary", "secretary", "omni", "archivist", "coder", "omni-auto",
]
TOOL_IDS = ["omni_actions", "omni_research"]


def attach_tools(t):
    for model_id in MODEL_IDS:
        status, model = call(f"/api/v1/models/model?id={model_id}", token=t)
        if status != 200 or not model:
            print(f"модель {model_id}: не найдена")
            continue
        meta = dict(model.get("meta") or {})
        existing = list(meta.get("toolIds") or [])
        for tool_id in TOOL_IDS:
            if tool_id not in existing:
                existing.append(tool_id)
        meta["toolIds"] = existing
        builtin = dict(meta.get("builtinTools") or {})
        builtin.update({"web_search": True, "knowledge": True, "memory": True})
        meta["builtinTools"] = builtin
        payload = {
            "id": model_id,
            "base_model_id": model.get("base_model_id"),
            "name": model.get("name") or model_id,
            "meta": meta,
            "params": dict(model.get("params") or {}),
            "is_active": model.get("is_active", True),
        }
        status, _ = call("/api/v1/models/model/update", "POST", payload, t)
        print(f"модель {model_id}: инструменты {status}")


MODELS = {
    "omni-reason": {
        "name": "Omni: размышление",
        "description": "Обдумывает задачу по шагам перед ответом. Без инструментов, экономит contexts.",
        "system": (
            "Ты — OmniSecretary в режиме Размышление. Сначала кратко разбери задачу: "
            "цель, известные факты, варианты и риски. Затем дай итоговый ответ. "
            "Не выдумывай факты. Отвечай по-русски. Если нужны актуальные данные, "
            "скажи, какой инструмент или источник следует использовать."
        ),
        "params": {"temperature": 0.3, "reasoning_effort": "high", "function_calling": "native"},
        "builtin": False,
    },
    "omni-research": {
        "name": "Omni: исследование",
        "description": "Исследует тему по нескольким запросам, собирает источники и делает сжатый отчёт.",
        "system": (
            "Ты — OmniSecretary в режиме Исследование. Используй инструмент research "
            "для сбора источников по нескольким запросам. Отвечай по-русски, структурированно: "
            "выводы, детали, источники. Не излагай лишнего, не повторяй один и тот же источник. "
            "Если источники противоречат друг другу — явно отметь это."
        ),
        "params": {"temperature": 0.4, "function_calling": "native"},
        "builtin": True,
        "tool_ids": ["omni_research", "omni_actions"],
    },
}


def sync_models(t):
    for model_id, spec in MODELS.items():
        status, model = call(f"/api/v1/models/model?id={model_id}", token=t)
        meta = dict(model.get("meta") or {}) if status == 200 else {}
        meta.update({
            "description": spec["description"],
            "capabilities": {"web_search": spec["builtin"], "citations": spec["builtin"],
                             "code_interpreter": False, "vision": False},
            "builtinTools": {"web_search": spec["builtin"], "knowledge": False,
                             "memory": False, "code_interpreter": False},
        })
        meta["toolIds"] = spec.get("tool_ids", [])
        payload = {
            "id": model_id,
            "base_model_id": "omni-auto",
            "name": spec["name"],
            "meta": meta,
            "params": {"system": spec["system"], **spec["params"]},
            "is_active": True,
        }
        if status == 200:
            endpoint = "/api/v1/models/model/update"
        else:
            endpoint = "/api/v1/models/create"
        status, _ = call(endpoint, "POST", payload, t)
        print(f"режим {model_id}: {status}")


def main():
    t = token()
    sync_tool(t, "omni_actions", "Действия OmniSecretary",
              "Календарь, задачи, заметки и Obsidian", "functions/omni_actions.py")
    sync_tool(t, "omni_research", "Исследование OmniSecretary",
              "Многошаговый веб-поиск с источниками", "functions/omni_research.py")
    attach_tools(t)
    sync_models(t)


if __name__ == "__main__":
    main()
