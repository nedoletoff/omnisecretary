"""
title: Действия OmniSecretary
author: OmniSecretary
version: 0.1.0
description: Календарь, задачи, заметки и Obsidian через портал от имени пользователя
"""

import httpx
from pydantic import BaseModel, Field

PORTAL = "http://portal:8000"


class Tools:
    class Valves(BaseModel):
        timeout: int = Field(default=20, ge=5, le=60)

    def __init__(self):
        self.valves = self.Valves()

    def _auth(self, request=None):
        if request is None:
            raise Exception("Нет контекста запроса")
        auth = request.headers.get("authorization", "")
        if not auth.lower().startswith("bearer "):
            raise Exception("Нужна авторизация Open WebUI")
        return {"Authorization": auth}

    def _call(self, method, path, request=None, body=None):
        try:
            with httpx.Client(timeout=self.valves.timeout) as client:
                r = client.request(method, PORTAL + path,
                                   headers=self._auth(request), json=body)
        except Exception as exc:
            return "Ошибка связи с порталом: " + str(exc)[:200]
        if r.status_code >= 400:
            try:
                detail = r.json().get("detail", r.text[:200])
            except Exception:
                detail = r.text[:200]
            return f"Ошибка {r.status_code}: {detail}"
        return r.text

    # --- Календарь ---
    def calendar_list(self, days: int = 7, __request__=None) -> str:
        """Показать события календаря на ближайшие дни.

        :param days: Сколько дней показать вперёд
        :return: Список событий
        """
        return self._call("GET", f"/api/m/events?days={days}", __request__)

    def calendar_create(self, title: str, start: str, duration_min: int = 60,
                        description: str = "", __request__=None) -> str:
        """Создать событие в календаре.

        :param title: Название события
        :param start: Начало в ISO 8601, например 2026-11-21T10:00
        :param duration_min: Длительность в минутах
        :param description: Описание
        :return: Результат создания
        """
        return self._call("POST", "/api/m/event", __request__, {
            "title": title, "start": start,
            "duration_min": duration_min, "description": description})

    def calendar_delete(self, event_id: str, __request__=None) -> str:
        """Удалить событие по ID.

        :param event_id: ID события
        :return: Результат удаления
        """
        return self._call("POST", f"/api/m/event/{event_id}/delete", __request__)

    # --- Задачи ---
    def task_list(self, show_completed: bool = False, __request__=None) -> str:
        """Показать задачи Vikunja.

        :param show_completed: Показывать выполненные
        :return: Список задач
        """
        return self._call("GET", f"/api/m/tasks?completed={str(show_completed).lower()}", __request__)

    def task_create(self, title: str, due: str = "", __request__=None) -> str:
        """Создать задачу.

        :param title: Название задачи
        :param due: Срок в формате YYYY-MM-DD или пусто
        :return: Результат создания
        """
        return self._call("POST", "/api/m/task", __request__, {"title": title, "due": due})

    def task_done(self, task_id: str, __request__=None) -> str:
        """Отметить задачу выполненной.

        :param task_id: ID задачи
        :return: Результат
        """
        return self._call("POST", f"/api/m/task/{task_id}/done", __request__)

    def task_delete(self, task_id: str, __request__=None) -> str:
        """Удалить задачу.

        :param task_id: ID задачи
        :return: Результат
        """
        return self._call("POST", f"/api/m/task/{task_id}/delete", __request__)

    # --- Заметки Open WebUI ---
    def notes_list(self, __request__=None) -> str:
        """Показать список заметок.

        :return: Список заметок
        """
        return self._call("GET", "/api/m/notes", __request__)

    def note_create(self, title: str, content: str, __request__=None) -> str:
        """Создать заметку на сервере.

        :param title: Заголовок
        :param content: Текст заметки
        :return: Результат создания
        """
        return self._call("POST", "/api/m/notes", __request__, {"title": title, "content": content})

    def note_delete(self, note_id: str, __request__=None) -> str:
        """Удалить заметку.

        :param note_id: ID заметки
        :return: Результат
        """
        return self._call("DELETE", f"/api/m/notes/{note_id}", __request__)

    # --- Obsidian (права проверяет сервер) ---
    def vault_files(self, __request__=None) -> str:
        """Показать файлы личного Obsidian-вольта.

        :return: Список файлов
        """
        return self._call("GET", "/api/m/vault/files", __request__)

    def vault_search(self, query: str, __request__=None) -> str:
        """Найти в личном Obsidian-вольте.

        :param query: Поисковый запрос
        :return: Найденные фрагменты
        """
        return self._call("GET", "/api/m/vault/search?q=" + httpx.QueryParams({"q": query})["q"], __request__)

    def vault_read(self, path: str, __request__=None) -> str:
        """Прочитать файл личного Obsidian-вольта.

        :param path: Путь к файлу внутри вольта
        :return: Содержимое
        """
        return self._call("GET", "/api/m/vault/read?path=" + httpx.QueryParams({"path": path})["path"], __request__)

    def vault_note(self, path: str, content: str, append: bool = False,
                   __request__=None) -> str:
        """Записать заметку в личный Obsidian-вольт.

        :param path: Относительный путь, например AI/Inbox/Идея.md
        :param content: Текст
        :param append: Дописать в конец вместо перезаписи
        :return: Результат записи
        """
        return self._call("POST", "/api/m/vault/note", __request__, {
            "path": path, "content": content, "append": append})
