"""Инструмент OmniSecretary: исследование темы по нескольким поисковым запросам."""

import httpx


class Tools:
    def deep_research(self, topic: str, queries: list[str], __request__=None) -> str:
        """Исследует тему по нескольким запросам и возвращает сжатый отчёт со ссылками.

        :param topic: Тема исследования
        :param queries: Список поисковых запросов (2–5)
        :return: Markdown-отчёт с источниками
        """
        token = ""
        if __request__ is not None:
            token = __request__.headers.get("Authorization", "")
        if not token:
            return "Ошибка: нет токена пользователя"

        results = []
        try:
            with httpx.Client(timeout=40) as client:
                for query in queries[:5]:
                    r = client.post(
                        "http://127.0.0.1:8080/api/v1/retrieval/process/web/search",
                        headers={"Authorization": token},
                        json={"queries": [query]},
                    )
                    if r.status_code != 200:
                        continue
                    body = r.json()
                    for item in body.get("items") or []:
                        results.append(item)
        except Exception as exc:
            return f"Ошибка поиска: {exc}"

        seen = set()
        lines = [f"# {topic}", ""]
        for item in results:
            link = item.get("link") or ""
            if not link or link in seen:
                continue
            seen.add(link)
            title = item.get("title") or link
            snippet = (item.get("snippet") or "").replace("\n", " ")[:240]
            lines.append(f"- [{title}]({link}) — {snippet}")
            if len(lines) >= 31:
                break
        if len(lines) == 2:
            lines.append("Источники не найдены.")
        return "\n".join(lines)
