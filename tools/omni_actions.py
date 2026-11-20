"""
title: OmniSecretary — действия
author: OmniSecretary
version: 1.0.0
description: Календарь, задачи, заметки и Obsidian от имени当前 пользователя
"""

import httpx

PORTAL = "http://portal:8000"


class Tools:
    """Действия в личных данных пользователя."""

    def __init__(self):
        pass

    def _auth(self, __request__=None):
        headers = {}
        if __request__ is not None:
            auth = __request__.headers.get("authorization")
            if auth:
                headers["Authorization"] = auth
        return headers

    def _get(self, path, __request__=None, **params):
        with httpx.Client(timeout=20) as client:
            r = client.get(PORTAL + path, headers=self._auth(__request__), params=params)
            r.raise_for_status()
            return r.json()

    def _post(self, path, payload=None, __request__=None):
        with httpx.Client(timeout=20) as client:
            r = client.post(PORTAL + path, json=payload or {}, headers=self._auth(__request__))
            r.raise_for_status()
            return r.json()

    def today(self, __request__=None) -> str:
        """Сводка дня: погода, события, задачи и заметки.

        :return: Markdown-сводка
        """
        d = self._get("/api/m/today", __request__)
        return "Сегодня:\n" + str(d)[:4000]

    def calendar_list(self, days: int = 7, __request__=None) -> str:
        """Список событий календаря на ближайшие дни.

        :param days: Сколько дней показать
        :return: Список событий
        """
        return str(self._get("/api/m/events", __request__, days=days))[:6000]

    def calendar_create(self, title: str, date: str, time: str = "09:00",
                        duration_minutes: int = 30, description: str = "",
                        __request__=None) -> str:
        """Создать событие в календаре.

        :param title: Название
        :param date: Дата в формате ГГГГ-ММ-ДД
        :param time: Время ЧЧ:ММ
        :param duration_minutes: Длительность в минутах
        :param description: Описание
        :return: Результат создания
        """
        return str(self._post("/api/m/event", {
            "title": title, "date": date, "time": time,
            "duration_minutes": duration_minutes, "description": description,
        }, __request__))[:2000]

    def calendar_delete(self, event_id: str, __request__=None) -> str:
        """Удалить событие по ID.

        :param event_id: ID события
        :return: Результат
        """
        return str(self._post(f"/api/m/event/{event_id}/delete", {}, __request__))[:1000]

    def tasks_list(self, __request__=None) -> str:
        """Список активных задач.

        :return: Список задач
        """
        return str(self._get("/api/m/tasks", __request__))[:6000]

    def task_create(self, title: str, due_date: str = "", description: str = "",
                    __request__=None) -> str:
        """Создать задачу.

        :param title: Название
        :param due_date: Срок в формате ГГГГ-ММ-ДД или пусто
        :param description: Описание
        :return: Результат
        """
        payload = {"title": title}
        if due_date:
            payload["due_date"] = due_date
        if description:
            payload["description"] = description
        return str(self._post("/api/m/task", payload, __request__))[:2000]

    def task_done(self, task_id: str, __request__=None) -> str:
        """Отметить задачу выполненной.

        :param task_id: ID задачи
        :return: Результат
        """
        return str(self._post(f"/api/m/task/{task_id}/done", {}, __request__))[:1000]

    def task_delete(self, task_id: str, __request__=None) -> str:
        """Удалить задачу.

        :param task_id: ID задачи
        :return: Результат
        """
        return str(self._post(f"/api/m/task/{task_id}/delete", {}, __request__))[:1000]

    def notes_list(self, __request__=None) -> str:
        """Список заметок Open WebUI.

        :return: Список заметок
        """
        return str(self._get("/api/m/notes", __request__))[:6000]

    def note_create(self, title: str, content: str, __request__=None) -> str:
        """Создать заметку на сервере.

        :param title: Заголовок
        :param content: Текст
        :return: Результат
        """
        return str(self._post("/api/m/notes", {"title": title, "content": content}, __request__))[:2000]

    def note_delete(self, note_id: str, __request__=None) -> str:
        """Удалить заметку.

        :param note_id: ID заметки
        :return: Результат
        """
        return str(self._post(f"/api/m/notes/{note_id}/delete", {}, __request__))[:1000]

    def vault_search(self, query: str, __request__=None) -> str:
        """Поиск по личному Obsidian-вольту (по правам пользователя).

        :param query: Поисковый запрос
        :return: Найденные фрагменты
        """
        return str(self._get("/api/m/vault/search", __request__, q=query))[:6000]

    def vault_read(self, path: str, __request__=None) -> str:
        """Прочитать файл из личного вольта.

        :param path: Пущ к файлу внутри вольта
        :return: Содержимое
        """
        return str(self._get("/api/m/vault/read", __request__, path=path))[:8000]

    def vault_note(self, path: str, content: str, append: bool = False,
                   __request__=None) -> str:
        """Записать заметку в личный вольт (только в разрешённые папки).

        :param path: Путь, например AI/Inbox/Заметка.md
        :param content: Текст
        :param append: Дополнить существующий файл
        :return: Результат с коммитом git
        """
        return str(self._post("/api/m/note", {
            "path": path, "content": content, "append": append,
        }, __request__))[:2000]
