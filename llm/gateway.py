"""OpenAI-совместимый шлюз: OpenRouter с откатом на бесплатную модель.

Зачем: у OpenRouter может закончиться баланс или ключ может быть отозван.
Тогда ассистент не должен «падать» — запрос уходит в бесплатный
Pollinations. Шлюз маленький (stdlib + fastapi/httpx уже есть в образе
omnisecretary-mcp), поэтому отдельный контейнер почти не ест память.

Модели, которые видит Open WebUI:
  omni-auto       — OpenRouter, при ошибке/402/429 — бесплатная
  omni-openrouter — только OpenRouter
  omni-free       — только бесплатная
  openai-fast, openai-large — алиасы бесплатной (старые чаты не ломаются)
"""

from __future__ import annotations

import json
import os
from typing import AsyncIterator

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

OPENROUTER_KEY = os.environ.get("OPENROUTER_KEY", "").strip()
OPENROUTER_BASE = os.environ.get("OPENROUTER_BASE", "https://openrouter.ai/api/v1").rstrip("/")
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "openai/gpt-4o-mini")
FALLBACK_BASE = os.environ.get("FALLBACK_BASE", "https://text.pollinations.ai/openai").rstrip("/")
FALLBACK_MODEL = os.environ.get("FALLBACK_MODEL", "openai-fast")
TIMEOUT = float(os.environ.get("GATEWAY_TIMEOUT", "120"))

MODELS = ["omni-auto", "omni-openrouter", "omni-free", "openai-fast", "openai-large"]
FALLBACK_STATUS = {401, 402, 403, 404, 429, 500, 502, 503, 504}

app = FastAPI(title="OmniSecretary LLM Gateway", version="1.0")


def target(model: str) -> tuple[str, str, str, bool]:
    """(base, api_key, model, allow_fallback)."""
    name = (model or "").lower()
    if name in ("omni-free", "openai-fast", "openai-large") or name.startswith("pollinations"):
        return FALLBACK_BASE, "pollinations", FALLBACK_MODEL, False
    if name == "omni-openrouter":
        return OPENROUTER_BASE, OPENROUTER_KEY, OPENROUTER_MODEL, False
    # omni-auto и любые прочие id — сначала OpenRouter.
    return OPENROUTER_BASE, OPENROUTER_KEY, OPENROUTER_MODEL, True


def headers_for(key: str) -> dict:
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    if key == OPENROUTER_KEY:
        headers["HTTP-Referer"] = os.environ.get("PUBLIC_URL", "https://localhost")
        headers["X-Title"] = "OmniSecretary"
    return headers


@app.get("/healthz")
def healthz() -> JSONResponse:
    return JSONResponse({"status": "ok", "openrouter": bool(OPENROUTER_KEY),
                         "model": OPENROUTER_MODEL, "fallback": FALLBACK_MODEL})


@app.get("/v1/models")
def models() -> JSONResponse:
    return JSONResponse({"object": "list", "data": [
        {"id": name, "object": "model", "owned_by": "omnisecretary"} for name in MODELS
    ]})


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
    requested = payload.get("model", "omni-auto")
    base, key, model, allow_fallback = target(requested)

    if key == OPENROUTER_KEY and not OPENROUTER_KEY:
        base, key, model, allow_fallback = FALLBACK_BASE, "pollinations", FALLBACK_MODEL, False

    try:
        client, response = await proxy(request, base, key, model)
    except Exception:  # noqa: BLE001 — сеть до OpenRouter
        if not allow_fallback:
            return JSONResponse({"error": {"message": "OpenRouter недоступен"}}, status_code=502)
        client, response = await proxy(request, FALLBACK_BASE, "pollinations", FALLBACK_MODEL)

    if allow_fallback and response.status_code in FALLBACK_STATUS:
        await response.aclose()
        await client.aclose()
        client, response = await proxy(request, FALLBACK_BASE, "pollinations", FALLBACK_MODEL)

    if response.status_code >= 400:
        text = (await response.aread()).decode("utf-8", "replace")
        await response.aclose()
        await client.aclose()
        return JSONResponse({"error": {"message": text[:500]}}, status_code=response.status_code)

    if payload.get("stream"):
        return StreamingResponse(stream_body(client, response),
                                 media_type="text/event-stream",
                                 headers={"X-Accel-Buffering": "no"})

    data = await response.aread()
    await response.aclose()
    await client.aclose()
    return JSONResponse(json.loads(data.decode("utf-8")), status_code=response.status_code)
