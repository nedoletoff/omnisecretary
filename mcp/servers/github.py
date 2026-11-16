#!/usr/bin/env python3
"""MCP-сервер GitHub (REST API, fine-grained PAT).

Токен — только из env. Минимальные права: Contents, Issues, Pull requests.
Никаких удалений репозиториев и правок настроек.
"""

from __future__ import annotations

import base64
import json
import os

import httpx
from mcp.server.fastmcp import FastMCP

TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()
API = "https://api.github.com"
# Запись (файлы, issues, PR) по умолчанию выключена: включение — осознанное
# действие владельца. Чтение работает всегда.
WRITE_ENABLED = os.environ.get("GITHUB_WRITE_ENABLED", "false").lower() in (
    "1", "true", "yes", "on")


def _write_denied() -> str:
    return ("Запись в GitHub отключена. Включить: GITHUB_WRITE_ENABLED=true "
            "в .env и make restart.")

mcp = FastMCP(
    "github",
    host="0.0.0.0",
    port=int(os.environ.get("MCP_PORT", "8000")),
    streamable_http_path="/mcp",
    stateless_http=True,
    instructions=(
        "GitHub: чтение и запись файлов, issues, pull requests. "
        "Сначала gh_list_repos или gh_search_code, потом точечные операции."
    ),
)


def _client() -> httpx.Client:
    return httpx.Client(
        base_url=API,
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        timeout=30,
    )


def _error(response: httpx.Response) -> str:
    try:
        return f"HTTP {response.status_code}: {response.json().get('message', '')}"
    except Exception:
        return f"HTTP {response.status_code}"


@mcp.tool()
def gh_list_repos(limit: int = 20) -> str:
    """Репозитории, доступные токену (имя, приватность, ветка по умолчанию)."""
    if not TOKEN:
        return "GITHUB_TOKEN не задан"
    with _client() as client:
        response = client.get("/user/repos", params={"per_page": max(1, min(int(limit), 100)),
                                                     "sort": "updated"})
        if response.status_code != 200:
            return _error(response)
        return "\n".join(
            f"{repo['full_name']} [{'private' if repo['private'] else 'public'}] "
            f"default={repo['default_branch']}"
            for repo in response.json()
        ) or "нет репозиториев"


@mcp.tool()
def gh_read_file(repo: str, path: str, ref: str = "") -> str:
    """Прочитать файл из репозитория (обрезано до 8000 символов)."""
    if not TOKEN:
        return "GITHUB_TOKEN не задан"
    with _client() as client:
        params = {"ref": ref} if ref else {}
        response = client.get(f"/repos/{repo}/contents/{path}", params=params)
        if response.status_code != 200:
            return _error(response)
        data = response.json()
        if isinstance(data, list):
            return "\n".join(f"{item['path']} ({item['type']})" for item in data)
        content = base64.b64decode(data.get("content", "")).decode("utf-8", "replace")
        return content[:8000] + ("" if len(content) <= 8000 else f"\n…(ещё {len(content) - 8000})")


@mcp.tool()
def gh_write_file(repo: str, path: str, content: str, message: str, branch: str = "") -> str:
    """Создать или обновить файл и сделать коммит."""
    if not WRITE_ENABLED:
        return _write_denied()
    if not TOKEN:
        return "GITHUB_TOKEN не задан"
    with _client() as client:
        params = {"ref": branch} if branch else {}
        existing = client.get(f"/repos/{repo}/contents/{path}", params=params)
        payload = {
            "message": message,
            "content": base64.b64encode(content.encode()).decode(),
        }
        if branch:
            payload["branch"] = branch
        if existing.status_code == 200 and not isinstance(existing.json(), list):
            payload["sha"] = existing.json()["sha"]
        response = client.put(f"/repos/{repo}/contents/{path}", json=payload)
        if response.status_code not in (200, 201):
            return _error(response)
        commit = response.json().get("commit", {})
        return f"коммит {commit.get('sha', '')[:7]}: {message}"


@mcp.tool()
def gh_list_issues(repo: str, state: str = "open", limit: int = 20) -> str:
    """Список issues (номер, заголовок, автор)."""
    if not TOKEN:
        return "GITHUB_TOKEN не задан"
    with _client() as client:
        response = client.get(f"/repos/{repo}/issues",
                              params={"state": state, "per_page": max(1, min(int(limit), 100))})
        if response.status_code != 200:
            return _error(response)
        return "\n".join(
            f"#{issue['number']} {issue['title']} ({issue['user']['login']})"
            for issue in response.json() if "pull_request" not in issue
        ) or "issues нет"


@mcp.tool()
def gh_create_issue(repo: str, title: str, body: str = "") -> str:
    """Создать issue."""
    if not WRITE_ENABLED:
        return _write_denied()
    if not TOKEN:
        return "GITHUB_TOKEN не задан"
    with _client() as client:
        response = client.post(f"/repos/{repo}/issues", json={"title": title, "body": body})
        if response.status_code not in (200, 201):
            return _error(response)
        return f"создан #{response.json()['number']}: {title}"


@mcp.tool()
def gh_create_pr(repo: str, title: str, head: str, base: str, body: str = "") -> str:
    """Создать pull request из head в base."""
    if not WRITE_ENABLED:
        return _write_denied()
    if not TOKEN:
        return "GITHUB_TOKEN не задан"
    with _client() as client:
        response = client.post(f"/repos/{repo}/pulls",
                               json={"title": title, "head": head, "base": base, "body": body})
        if response.status_code not in (200, 201):
            return _error(response)
        return f"создан PR #{response.json()['number']}: {response.json()['html_url']}"


@mcp.tool()
def gh_search_code(query: str, limit: int = 10) -> str:
    """Поиск по коду (нужен scope read:org или code search в PAT)."""
    if not TOKEN:
        return "GITHUB_TOKEN не задан"
    with _client() as client:
        response = client.get("/search/code",
                              params={"q": query, "per_page": max(1, min(int(limit), 30))})
        if response.status_code != 200:
            return _error(response)
        return "\n".join(f"{item['repository']['full_name']}:{item['path']}"
                         for item in response.json().get("items", [])) or "ничего не найдено"


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
