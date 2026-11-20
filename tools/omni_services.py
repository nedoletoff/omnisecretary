"""Инструменты OmniSecretary: календарь, задачи, заметки, Obsidian.

Все запросы идут в портал с токеном текущего пользователя, поэтому
изоляция и права папок Obsidian проверяются сервером портала.
"""

import json
import urllib.parse

import httpx

PORTAL = "http://portal:8000"


def _token(__request__=None):
    if __request__ is None:
        return ""
    return __request__.headers.get("authorization", "")


def _call(method, path, __request__=None, body=None):
    token = _token(__request__)
    if not token:
        return {"error": "Нет токена пользователя"}
    try:
        with httpx.Client(timeout=20) as client:
            response = client.request(
                method, PORTAL + path, headers={"Authorization": token}, json=body
            )
        try:
            return response.json()
        except Exception:
            return {"http": response.status_code, "text": response.text[:500]}
    except Exception as e:
        return {"error": str(e)[:200]}


def _compact(data, limit=1800):
    text = json.dumps(data, ensure_ascii=False, indent=1)
    return text if len(text) <= limit else text[:limit] + "\n… (обрезано)"


class Tools:
    def today(self, __request__=None) -> str:
        """Сводка дня: погода, события, задачи, заметки и напоминания."""
        return _compact(_call("GET", "/api/m/today", __request__), 2500)

    # Календарь
    def calendar_list(self, days: int = 7, __request__=None) -> str:
        """Список событий календаря на ближайшие дни.
        :param days: Сколько дней показать
        """
        return _compact(_call("GET", f"/api/m/events?days={int(days)}", __request__), 2500)

    def calendar_create(self, title: str, date: str, time: str = "10:00",
                        duration: int = 60, description: str = "",
                        __request__=None) -> str:
        """Создать событие в календаре. Дата в формате ГГГГ-ММ-ДД, время ЧЧ:ММ.
        :param title: Название события
        :param date: Дата ГГГГ-ММ-ДД
        :param time: Время ЧЧ:ММ
        :param duration: Длительность в минутах
        :param description: Описание
        """
        return _compact(_call("POST", "/api/m/event", __request__, {
            "title": title, "date": date, "time": time,
            "duration": int(duration), "description": description,
        }))

    def calendar_update(self, event_id: str, title: str = "", date: str = "",
                        time: str = "", duration: int = 0, description: str = "",
                        __request__=None) -> str:
        """Изменить событие. Передавайте только поля, которые нужно обновить.
        :param event_id: ID события
        :param title: Новое название
        :param date: Новая дата ГГГГ-ММ-ДД
        :param time: Новое время ЧЧ:ММ
        :param duration: Новая длительность в минутах
        :param description: Новое описание
        """
        body = {}
        for key, value in (("title", title), ("date", date), ("time", time),
                           ("duration", duration), ("description", description)):
            if value:
                body[key] = value
        return _compact(_call("POST", f"/api/m/event/{event_id}/update", __request__, body))

    def calendar_delete(self, event_id: str, __request__=None) -> str:
        """Удалить событие по ID.
        :param event_id: ID события
        """
        return _compact(_call("POST", f"/api/m/event/{event_id}/delete", __request__, {}))

    # Задачи
    def tasks_list(self, __request__=None) -> str:
        """Список задач Vikunja."""
        return _compact(_call("GET", "/api/m/tasks", __request__), 2500)

    def task_create(self, title: str, due_date: str = "", description: str = "",
                    __request__=None) -> str:
        """Создать задачу.
        :param title: Название задачи
        :param due_date: Срок в формате ГГГГ-ММ-ДД (необязательно)
        :param description: Описание
        """
        return _compact(_call("POST", "/api/m/task", __request__, {
            "title": title, "due_date": due_date, "description": description,
        }))

    def task_done(self, task_id: int, __request__=None) -> str:
        """Отметить задачу выполненной.
        :param task_id: ID задачи
        """
        return _compact(_call("POST", f"/api/m/task/{int(task_id)}/done", __request__, {}))

    def task_update(self, task_id: int, title: str = "", due_date: str = "",
                    __request__=None) -> str:
        """Изменить название или срок задачи.
        :param task_id: ID задачи
        :param title: Новое название
        :param due_date: Новый срок ГГГГ-ММ-ДД
        """
        body = {}
        if title:
            body["title"] = title
        if due_date:
            body["due_date"] = due_date
        return _compact(_call("POST", f"/api/m/task/{int(task_id)}/update", __request__, body))

    def task_delete(self, task_id: int, __request__=None) -> str:
        """Удалить задачу.
        :param task_id: ID задачи
        """
        return _compact(_call("POST", f"/api/m/task/{int(task_id)}/delete", __request__, {}))

    # Заметки Open WebUI
    def notes_list(self, __request__=None) -> str:
        """Список заметок пользователя."""
        return _compact(_call("GET", "/api/m/notes", __request__), 2500)

    def note_create(self, title: str, content: str, __request__=None) -> str:
        """Создать заметку на сервере (доступна в вебе и приложении).
        :param title: Заголовок
        :param content: Текст в Markdown
        """
        return _compact(_call("POST", "/api/m/notes", __request__, {
            "title": title, "content": content,
        }))

    def note_update(self, note_id: str, title: str = "", content: str = "",
                    __request__=None) -> str:
        """Изменить заметку.
        :param note_id: ID заметки
        :param title: Новый заголовок
        :param content: Новый текст
        """
        body = {}
        if title:
            body["title"] = title
        if content:
            body["content"] = content
        return _compact(_call("POST", f"/api/m/notes/{note_id}/update", __request__, body))

    def note_delete(self, note_id: str, __request__=None) -> str:
        """Удалить заметку.
        :param note_id: ID заметки
        """
        return _compact(_call("POST", f"/api/m/notes/{note_id}/delete", __request__, {}))

    # Obsidian
    def vault_status(self, __request__=None) -> str:
        """Состояние личного Obsidian-вольта пользователя."""
        return _compact(_call("GET", "/api/m/vault/config", __request__), 1200)

    def vault_search(self, query: str, __request__=None) -> str:
        """Поиск по личному Obsidian-вольту (только разрешённые папки).
        :param query: Поисковый запрос
        """
        return _compact(_call("GET", "/api/m/vault/search?q=" + urllib.parse.quote(query), __request__), 2500)

    def vault_read(self, path: str, __request__=None) -> str:
        """Прочитать заметку Obsidian по пути (если чтение разрешено).
        :param path: Относительный путь к файлу
        """
        return _compact(_call("GET", "/api/m/vault/read?path=" + urllib.parse.quote(path), __request__), 3000)

    def vault_note(self, text: str, path: str = "AI/Inbox/Заметка.md",
                   append: bool = True, __request__=None) -> str:
        """Записать заметку в личный Obsidian-вольт (коммит и push в GitHub).
        :param text: Текст заметки
        :param path: Путь внутри вольта
        :param append: Дополнить файл, а не перезаписать
        """
        return _compact(_call("POST", "/api/m/note", __request__, {
            "text": text, "path": path, "append": bool(append),
        }))
