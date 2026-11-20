"""Инструменты OmniSecretary: календарь, задачи, заметки, вольт."""

import httpx

PORTAL = "http://portal:8000"


class Tools:
    def _call(self, method: str, path: str, __request__=None, **kwargs) -> str:
        token = ""
        if __request__ is not None:
            token = __request__.headers.get("Authorization", "")
        if not token:
            return "Ошибка: нет токена пользователя"
        try:
            with httpx.Client(timeout=20) as client:
                response = client.request(
                    method, PORTAL + path,
                    headers={"Authorization": token}, **kwargs
                )
            body = response.text[:4000]
            return f"HTTP {response.status_code}\n{body}"
        except Exception as exc:
            return f"Ошибка запроса к порталу: {exc}"

    def today(self, __request__=None) -> str:
        """Сводка дня: погода, события, задачи, заметки."""
        return self._call("GET", "/api/m/today", __request__)

    def events(self, days: int = 7, __request__=None) -> str:
        """Список событий календаря на ближайшие дни."""
        return self._call("GET", f"/api/m/events?days={days}", __request__)

    def create_event(self, title: str, start_at: str, end_at: str = "",
                     description: str = "", __request__=None) -> str:
        """Создать событие в календаре. Время ISO 8601, например 2026-11-21T10:00:00."""
        payload = {"title": title, "start_at": start_at}
        if end_at:
            payload["end_at"] = end_at
        if description:
            payload["description"] = description
        return self._call("POST", "/api/m/event", __request__, json=payload)

    def tasks(self, __request__=None) -> str:
        """Список задач Vikunja."""
        return self._call("GET", "/api/m/tasks", __request__)

    def create_task(self, title: str, description: str = "", __request__=None) -> str:
        """Создать задачу Vikunja."""
        return self._call("POST", "/api/m/task", __request__,
                          json={"title": title, "description": description})

    def notes(self, __request__=None) -> str:
        """Список заметок Open WebUI."""
        return self._call("GET", "/api/m/notes", __request__)

    def create_note(self, title: str, content: str, __request__=None) -> str:
        """Создать заметку Open WebUI."""
        return self._call("POST", "/api/m/notes", __request__,
                          json={"title": title, "content": content})

    def vault_search(self, query: str, __request__=None) -> str:
        """Поиск по персональному Obsidian-вольту."""
        return self._call("GET", "/api/m/vault/search", __request__,
                          params={"q": query})

    def vault_note(self, path: str, content: str, append: bool = False,
                   __request__=None) -> str:
        """Записать или дополнить заметку в Obsidian-вольте (только в разрешённые папки)."""
        return self._call("POST", "/api/m/vault/note", __request__,
                          json={"path": path, "content": content, "append": append})
