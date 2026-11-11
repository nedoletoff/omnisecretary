#!/usr/bin/env python3
"""MCP-сервер Vikunja: задачи на естественном языке.

Токен Vikunja — только из env (создаётся scripts/init-vikunja.sh).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import httpx
from mcp.server.fastmcp import FastMCP

BASE = os.environ.get("VIKUNJA_URL", "http://vikunja:3456").rstrip("/") + "/api/v1"
TOKEN = os.environ.get("VIKUNJA_API_TOKEN", "").strip()

mcp = FastMCP(
    "vikunja",
    host="0.0.0.0",
    port=int(os.environ.get("MCP_PORT", "8000")),
    streamable_http_path="/mcp",
    stateless_http=True,
    instructions=(
        "Задачи Vikunja. Сначала vk_projects и vk_tasks, потом создание/правка. "
        "Даты — ISO 8601 (2026-11-10T18:00:00Z)."
    ),
)


def _client() -> httpx.Client:
    return httpx.Client(
        base_url=BASE,
        headers={"Authorization": f"Bearer {TOKEN}", "Accept": "application/json"},
        timeout=30,
    )


def _error(response: httpx.Response) -> str:
    try:
        return f"HTTP {response.status_code}: {response.json().get('message', '')}"
    except Exception:
        return f"HTTP {response.status_code}"


def _task_line(task: dict) -> str:
    due = task.get("due_date") or ""
    due = due[:16].replace("T", " ") if due and not due.startswith("0001") else ""
    mark = "✓" if task.get("done") else "•"
    return f"{mark} #{task['id']} {task['title']}" + (f" (до {due})" if due else "")


@mcp.tool()
def vk_projects() -> str:
    """Список проектов (id и название)."""
    if not TOKEN:
        return "VIKUNJA_API_TOKEN не задан"
    with _client() as client:
        response = client.get("/projects")
        if response.status_code != 200:
            return _error(response)
        return "\n".join(f"#{p['id']} {p['title']}" for p in response.json()) or "проектов нет"


@mcp.tool()
def vk_tasks(project_id: int, include_done: bool = False, limit: int = 30) -> str:
    """Задачи проекта (по умолчанию только незавершённые)."""
    if not TOKEN:
        return "VIKUNJA_API_TOKEN не задан"
    with _client() as client:
        response = client.get(f"/projects/{int(project_id)}/tasks",
                              params={"per_page": max(1, min(int(limit), 100))})
        if response.status_code != 200:
            return _error(response)
        tasks = [t for t in response.json() if include_done or not t.get("done")]
        return "\n".join(_task_line(t) for t in tasks) or "задач нет"


def _default_project() -> int:
    """Проект по умолчанию: «Inbox», иначе первый доступный."""
    with _client() as client:
        response = client.get("/projects")
        if response.status_code != 200:
            return 1
        projects = response.json() or []
    for project in projects:
        if str(project.get("title", "")).lower() == "inbox":
            return int(project["id"])
    return int(projects[0]["id"]) if projects else 1


@mcp.tool()
def vk_create_task(title: str, project_id: int = 0, description: str = "",
                   due_date: str = "", priority: int = 0) -> str:
    """Создать задачу. project_id=0 — проект Inbox. due_date — ISO 8601."""
    if not TOKEN:
        return "VIKUNJA_API_TOKEN не задан"
    if not project_id:
        project_id = _default_project()
    payload = {
        "title": title,
        "description": description,
        "priority": max(0, min(int(priority), 5)),
    }
    if due_date:
        # Vikunja ждёт RFC3339; дата без времени ломает валидацию.
        if len(due_date) == 10:
            due_date = due_date + "T00:00:00Z"
        payload["due_date"] = due_date
    with _client() as client:
        response = client.put(f"/projects/{int(project_id)}/tasks", json=payload)
        if response.status_code not in (200, 201):
            return _error(response)
        task = response.json()
        return f"создана {_task_line(task)}"


@mcp.tool()
def vk_update_task(task_id: int, title: str = "", description: str = "",
                   due_date: str = "", priority: int = -1, done: bool | None = None) -> str:
    """Изменить задачу: передавайте только нужные поля."""
    if not TOKEN:
        return "VIKUNJA_API_TOKEN не задан"
    payload: dict = {}
    if title:
        payload["title"] = title
    if description:
        payload["description"] = description
    if due_date:
        payload["due_date"] = due_date
    if priority >= 0:
        payload["priority"] = min(int(priority), 5)
    if done is not None:
        payload["done"] = bool(done)
    if not payload:
        return "нечего менять"
    with _client() as client:
        response = client.post(f"/tasks/{int(task_id)}", json=payload)
        if response.status_code != 200:
            return _error(response)
        return f"обновлена {_task_line(response.json())}"


@mcp.tool()
def vk_complete_task(task_id: int) -> str:
    """Отметить задачу выполненной."""
    return vk_update_task(task_id, done=True)


@mcp.tool()
def vk_today() -> str:
    """Мои задачи на сегодня и просроченные (по всем проектам)."""
    if not TOKEN:
        return "VIKUNJA_API_TOKEN не задан"
    with _client() as client:
        response = client.get("/tasks", params={"filter": "done = false", "per_page": 100})
        if response.status_code != 200:
            return _error(response)
        today = datetime.now(timezone.utc).date()
        lines = []
        for task in response.json():
            due = task.get("due_date") or ""
            if not due or due.startswith("0001"):
                continue
            try:
                due_date = datetime.fromisoformat(due.replace("Z", "+00:00")).date()
            except ValueError:
                continue
            if due_date <= today:
                lines.append(("просрочено: " if due_date < today else "") + _task_line(task))
        return "\n".join(lines) or "на сегодня ничего не просрочено"


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
