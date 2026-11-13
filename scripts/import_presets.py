#!/usr/bin/env python3
"""Импорт пресетов-«лиц» OmniSecretary в Open WebUI.

Читает presets/*.md (front matter + системный промпт) и отправляет их
в POST /api/v1/models/import. Повторный импорт обновляет пресеты по id.

Примеры:
    WEBUI_API_KEY=sk-... ./scripts/import-presets.sh --base-model deepseek-chat
    ./scripts/import-presets.sh --dry-run          # только собрать models.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PRESETS_DIR = ROOT / "presets"

CAPABILITY_KEYS = (
    "citations",
    "web_search",
    "code_interpreter",
    "vision",
    "image_generation",
)


def load_dotenv(path: Path) -> dict[str, str]:
    """Минимальный парсер .env (без внешних зависимостей)."""
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


def to_bool(value: str) -> bool:
    return value.strip().lower() in {"true", "yes", "1", "on"}


def to_number(value: str) -> float | int:
    try:
        return int(value)
    except ValueError:
        return float(value)


def parse_preset(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        raise ValueError(f"{path.name}: нет front matter (--- в первой строке)")
    end = text.find("\n---", 3)
    if end == -1:
        raise ValueError(f"{path.name}: front matter не закрыт")

    front = text[3:end].strip("\n")
    body = text[end + 4 :].lstrip("\n").rstrip() + "\n"

    meta: dict[str, object] = {}
    for line in front.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            raise ValueError(f"{path.name}: строка без ':' → {line!r}")
        key, value = line.split(":", 1)
        key, value = key.strip(), value.strip()
        if value.lower() in {"true", "false"}:
            meta[key] = to_bool(value)
        elif key == "temperature":
            meta[key] = to_number(value)
        else:
            meta[key] = value

    for required in ("id", "name"):
        if not meta.get(required):
            raise ValueError(f"{path.name}: не задано поле {required}")

    capabilities = {k: bool(meta[k]) for k in CAPABILITY_KEYS if k in meta}
    params: dict[str, object] = {}
    # Системный промпт живёт в params.system: именно оттуда его берёт
    # фронтенд Open WebUI (meta.system нигде не применяется).
    params["system"] = body
    if "temperature" in meta:
        params["temperature"] = meta["temperature"]
    # legacy включает RAG-поиск (features.web_search) для моделей без tool calling
    if "function_calling" in meta:
        params["function_calling"] = meta["function_calling"]

    # tools: vikunja, files → meta.toolIds = ["server:mcp:vikunja", ...]
    # Пустое значение означает «без инструментов» (приватный режим).
    tools_raw = str(meta.get("tools", "") or "").strip()
    tool_ids = [
        f"server:mcp:{name.strip()}"
        for name in tools_raw.split(",")
        if name.strip()
    ]

    return {
        "id": meta["id"],
        "name": meta["name"],
        "meta": {
            "description": meta.get("description", ""),
            "capabilities": capabilities,
            "toolIds": tool_ids,
        },
        "params": params,
        "_file": path.name,
    }


def build_models(base_model: str | None) -> list[dict]:
    presets = []
    for path in sorted(PRESETS_DIR.glob("*.md")):
        if path.name.lower() == "readme.md":
            continue
        presets.append(parse_preset(path))
    if not presets:
        raise SystemExit("Не найдено ни одного пресета в presets/*.md")

    models = []
    for preset in presets:
        preset.pop("_file", None)
        models.append(
            {
                "id": preset["id"],
                "name": preset["name"],
                "base_model_id": base_model,
                "meta": preset["meta"],
                "params": preset["params"],
                "is_active": True,
            }
        )
    return models


def post_import(api_url: str, api_key: str, models: list[dict]) -> None:
    url = api_url.rstrip("/") + "/api/v1/models/import"
    payload = json.dumps({"models": models}).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise SystemExit(f"Open WebUI вернул HTTP {exc.code}: {detail[:400]}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"Не удалось подключиться к {url}: {exc.reason}") from exc
    print(f"Импорт выполнен: {body}")


def apply_model_fields(api_url: str, api_key: str, models: list[dict]) -> None:
    """Дозаписывает params и meta: /models/import их не сохраняет в v0.13.3."""
    url = api_url.rstrip("/") + "/api/v1/models/model/update"
    for model in models:
        payload = json.dumps({
            "id": model["id"],
            "name": model.get("name", model["id"]),
            "params": model.get("params") or {},
            "meta": model.get("meta") or {},
        }).encode("utf-8")
        request = urllib.request.Request(
            url, data=payload, method="POST",
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {api_key}"},
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            raise SystemExit(
                f"Обновление {model['id']}: HTTP {exc.code}: {detail[:300]}") from exc


def apply_base_persona(api_url: str, api_key: str, base_model: str,
                       models: list[dict]) -> None:
    """Персонаж OmniSecretary для самой базовой модели.

    Пользователь может выбрать в списке не пресет, а базовую модель —
    тогда без этого шага ассистент считает себя обычным чатом.
    """
    persona = next((m for m in models if m["id"] == "omnisecretary"), None)
    if not persona:
        print("[warn] пресет omnisecretary не найден — базовая модель без персонажа")
        return
    payload = json.dumps({
        "id": base_model,
        "name": base_model,
        "params": {"temperature": persona.get("params", {}).get("temperature", 0.5),
                   "system": persona["params"]["system"],
                   "function_calling": "legacy"},
        "meta": {
            "description": "Базовый чат OmniSecretary: тот же ассистент без пресета.",
            "capabilities": persona["meta"].get("capabilities", {}),
        },
    }).encode("utf-8")
    url = api_url.rstrip("/") + "/api/v1/models/model/update?id=" + base_model
    request = urllib.request.Request(
        url, data=payload, method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {api_key}"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            response.read()
        print(f"[ok] персонаж OmniSecretary применён к базовой модели {base_model}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        print(f"[warn] базовая модель {base_model}: HTTP {exc.code} {detail[:200]}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Импорт пресетов OmniSecretary")
    parser.add_argument(
        "--base-model",
        default=os.environ.get("PRESET_BASE_MODEL", ""),
        help="id базовой модели в Open WebUI (например, deepseek-chat)",
    )
    parser.add_argument(
        "--api-url",
        default="",
        help="адрес Open WebUI (по умолчанию WEBUI_URL из .env)",
    )
    parser.add_argument(
        "--api-key",
        default=os.environ.get("WEBUI_API_KEY", ""),
        help="API-ключ Open WebUI (или переменная WEBUI_API_KEY)",
    )
    parser.add_argument(
        "--no-base-persona",
        action="store_true",
        help="не применять персонаж OmniSecretary к базовой модели",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="только собрать presets/models.json, ничего не отправлять",
    )
    args = parser.parse_args()

    env = load_dotenv(ROOT / ".env")
    api_url = args.api_url or env.get("WEBUI_URL", "http://127.0.0.1")
    base_model = args.base_model or None

    models = build_models(base_model)

    if args.dry_run:
        out = PRESETS_DIR / "models.json"
        out.write_text(
            json.dumps({"models": models}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"Собрано {len(models)} пресетов → {out}")
        for model in models:
            print(f"  - {model['id']}: {model['name']}")
        return 0

    if not args.api_key:
        raise SystemExit(
            "Не задан API-ключ. Создайте его в Open WebUI (Settings → Account → "
            "API Keys) и передайте WEBUI_API_KEY=... или используйте --dry-run."
        )
    if not base_model:
        raise SystemExit(
            "Не задана базовая модель. Сначала добавьте провайдера LLM в Open WebUI "
            "и передайте --base-model <id модели>."
        )

    post_import(api_url, args.api_key, models)
    apply_model_fields(api_url, args.api_key, models)
    if base_model and not args.no_base_persona:
        apply_base_persona(api_url, args.api_key, base_model, models)
    print(f"Готово: {len(models)} пресетов.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
