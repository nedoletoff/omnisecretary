"""OpenAI-совместимый шлюз: провайдеры, OpenRouter и откат на бесплатную модель.

Зачем: Open WebUI не хранит персональные ключи LLM для пользователей. Шлюз
читает конфиг портала (/data/llm-config.json), определяет пользователя по
подписанному JWT-заголовку Open WebUI и использует его провайдер; если личного
нет — общесистемный; если и его нет — OpenRouter из .env; при ошибке —
бесплатная модель. Ни один ключ не попадает в браузер.

Модели, которые видит Open WebUI:
  omni-auto       — личный/системный провайдер, затем OpenRouter, затем бесплатная
  omni-openrouter — только OpenRouter из .env
  omni-free       — только бесплатная модель
  openai-fast, openai-large — алиасы бесплатной (старые чаты не ломаются)
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from pathlib import Path
from typing import Any, AsyncIterator

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

OPENROUTER_KEY = os.environ.get("OPENROUTER_KEY", "").strip()
OPENROUTER_BASE = os.environ.get("OPENROUTER_BASE", "https://openrouter.ai/api/v1").rstrip("/")
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "openai/gpt-4o-mini")
FALLBACK_BASE = os.environ.get("FALLBACK_BASE", "https://text.pollinations.ai/openai").rstrip("/")
FALLBACK_MODEL = os.environ.get("FALLBACK_MODEL", "openai-fast")
TIMEOUT = float(os.environ.get("GATEWAY_TIMEOUT", "120"))

CONFIG_PATH = Path(os.environ.get("LLM_CONFIG_PATH", "/data/llm-config.json"))
JWT_SECRET = os.environ.get("FORWARD_USER_INFO_HEADER_JWT_SECRET", "").strip()
JWT_HEADER = os.environ.get("FORWARD_USER_INFO_HEADER_JWT", "X-OpenWebUI-User-Jwt")

MODELS = ["omni-auto", "omni-openrouter", "omni-free", "openai-fast", "openai-large"]
FALLBACK_STATUS = {401, 402, 403, 404, 429, 500, 502, 503, 504}
FREE_NAMES = {"omni-free", "openai-fast", "openai-large"}

app = FastAPI(title="OmniSecretary LLM Gateway", version="1.1")
_cfg_cache: dict[str, Any] = {"mtime": -1.0, "data": {}}


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def verify_jwt(value: str) -> str:
    """Проверяем HS256 вручную: PyJWT в образе может отсутствовать."""
    if not JWT_SECRET or not value:
        return ""
    parts = value.split(".")
    if len(parts) != 3:
        return ""
    expected = hmac.new(JWT_SECRET.encode(), f"{parts[0]}.{parts[1]}".encode(),
                        hashlib.sha256).digest()
    if not hmac.compare_digest(expected, _b64decode(parts[2])):
        return ""
    try:
        payload = json.loads(_b64decode(parts[1]))
    except Exception:  # noqa: BLE001 — некорректный JWT просто игнорируем
        return ""
    if float(payload.get("exp", 0)) < time.time():
        return ""
    return str(payload.get("sub", ""))


def load_config() -> dict:
    """Конфиг меняется редко, поэтому кэшируем по mtime файла."""
    try:
        mtime = CONFIG_PATH.stat().st_mtime
    except OSError:
        return {}
    if mtime != _cfg_cache["mtime"]:
        try:
            _cfg_cache["data"] = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — битый конфиг не должен ронять чат
            _cfg_cache["data"] = {}
        _cfg_cache["mtime"] = mtime
    return _cfg_cache["data"]


def resolve(request: Request, model: str) -> dict:
    """(base, key, model, allow_fallback, source, user_id)."""
    name = (model or "omni-auto").lower()
    user_id = verify_jwt(request.headers.get(JWT_HEADER, ""))
    cfg = load_config()

    if name in FREE_NAMES or name.startswith("pollinations"):
        return {"base": FALLBACK_BASE, "key": "pollinations", "model": FALLBACK_MODEL,
                "fallback": False, "source": "бесплатная", "user": user_id}

    if name == "omni-openrouter":
        return {"base": OPENROUTER_BASE, "key": OPENROUTER_KEY,
                "model": OPENROUTER_MODEL, "fallback": False,
                "source": "OpenRouter (.env)", "user": user_id}

    personal = (cfg.get("users") or {}).get(user_id) if user_id else None
    if personal and personal.get("enabled") and personal.get("base_url"):
        return {"base": personal["base_url"].rstrip("/"),
                "key": personal.get("api_key", ""),
                "model": personal.get("model") or OPENROUTER_MODEL,
                "fallback": bool(personal.get("fallback", True)),
                "source": f"личный ({personal.get('name') or 'провайдер'})",
                "user": user_id}

    system = cfg.get("system")
    if system and system.get("enabled") and system.get("base_url"):
        return {"base": system["base_url"].rstrip("/"),
                "key": system.get("api_key", ""),
                "model": system.get("model") or OPENROUTER_MODEL,
                "fallback": bool(system.get("fallback", True)),
                "source": f"системный ({system.get('name') or 'провайдер'})",
                "user": user_id}

    if OPENROUTER_KEY:
        return {"base": OPENROUTER_BASE, "key": OPENROUTER_KEY,
                "model": OPENROUTER_MODEL, "fallback": True,
                "source": "OpenRouter (.env)", "user": user_id}

    return {"base": FALLBACK_BASE, "key": "pollinations", "model": FALLBACK_MODEL,
            "fallback": False, "source": "бесплатная", "user": user_id}


def headers_for(key: str) -> dict:
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    if key and (key == OPENROUTER_KEY or "openrouter.ai" in key):
        headers["HTTP-Referer"] = os.environ.get("PUBLIC_URL", "https://localhost")
        headers["X-Title"] = "OmniSecretary"
    return headers


@app.get("/healthz")
def healthz() -> JSONResponse:
    cfg = load_config()
    return JSONResponse({
        "status": "ok",
        "openrouter": bool(OPENROUTER_KEY),
        "model": OPENROUTER_MODEL,
        "fallback": FALLBACK_MODEL,
        "config": CONFIG_PATH.exists(),
        "system_provider": bool((cfg.get("system") or {}).get("enabled")),
        "personal_providers": sum(1 for v in (cfg.get("users") or {}).values()
                                  if v.get("enabled")),
        "jwt": bool(JWT_SECRET),
    })


@app.get("/v1/models")
def models() -> JSONResponse:
    return JSONResponse({"object": "list", "data": [
        {"id": name, "object": "model", "owned_by": "omnisecretary"} for name in MODELS
    ]})


@app.get("/v1/resolve")
def resolve_debug(request: Request) -> JSONResponse:
    """diag: какой провайдер выбран для пользователя (без вывода ключа)."""
    chosen = resolve(request, request.query_params.get("model", "omni-auto"))
    return JSONResponse({
        "source": chosen["source"],
        "base_url": chosen["base"],
        "model": chosen["model"],
        "has_key": bool(chosen["key"]),
        "fallback": chosen["fallback"],
        "user": chosen["user"],
    })


async def proxy(request: Request, base: str, key: str, model: str):
    body = await request.json()
    body["model"] = model
    client = httpx.AsyncClient(timeout=TIMEOUT)
    url = f"{base}/chat/completions"
    headers = headers_for(key)
    if body.get("stream"):
        response = await client.send(
            client.build_request("POST", url, json=body, headers=headers), stream=True)
        return client, response
    response = await client.post(url, json=body, headers=headers)
    return client, response


async def stream_body(client: httpx.AsyncClient, response: httpx.Response) -> AsyncIterator[bytes]:
    try:
        async for chunk in response.aiter_raw():
            yield chunk
    finally:
        await response.aclose()
        await client.aclose()


@app.post("/v1/chat/completions")
async def chat(request: Request):
    payload = await request.json()
    chosen = resolve(request, payload.get("model", "omni-auto"))
    print(f"[llm] model={payload.get('model', '-')} source={chosen['source']} "
          f"user={chosen['user'] or '-'}", flush=True)

    try:
        client, response = await proxy(
            request, chosen["base"], chosen["key"], chosen["model"])
    except Exception:  # noqa: BLE001 — сеть до провайдера
        if not chosen["fallback"]:
            return JSONResponse({"error": {"message": "Провайдер недоступен"}}, status_code=502)
        client, response = await proxy(
            request, FALLBACK_BASE, "pollinations", FALLBACK_MODEL)
        chosen["source"] = "бесплатная (откат)"

    if chosen["fallback"] and response.status_code in FALLBACK_STATUS:
        await response.aclose()
        await client.aclose()
        client, response = await proxy(
            request, FALLBACK_BASE, "pollinations", FALLBACK_MODEL)
        chosen["source"] = "бесплатная (откат)"

    source_header = chosen["source"].encode("ascii", "replace").decode()

    if response.status_code >= 400:
        text = (await response.aread()).decode("utf-8", "replace")
        await response.aclose()
        await client.aclose()
        return JSONResponse({"error": {"message": text[:500]}}, status_code=response.status_code,
                            headers={"X-Omni-Source": source_header})

    if payload.get("stream"):
        return StreamingResponse(stream_body(client, response),
                                 media_type="text/event-stream",
                                 headers={"X-Accel-Buffering": "no",
                                          "X-Omni-Source": source_header})

    data = await response.aread()
    await response.aclose()
    await client.aclose()
    return JSONResponse(json.loads(data.decode("utf-8")), status_code=response.status_code,
                        headers={"X-Omni-Source": source_header})
