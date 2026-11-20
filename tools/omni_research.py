"""Инструмент исследования: несколько поисковых запросов и сжатый отчёт с источниками."""

import asyncio
import json

import httpx
from pydantic import BaseModel, Field


class Tools:
    class Valves(BaseModel):
        max_queries: int = Field(default=5, ge=1, le=8)
        max_results: int = Field(default=12, ge=1, le=30)
        timeout: int = Field(default=35, ge=10, le=120)

    def __init__(self):
        self.valves = self.Valves()

    async def deep_research(self, topic: str, queries: list[str] = [],
                            __request__=None) -> str:
        """Исследует тему по нескольким запросам и возвращает отчёт со ссылками.
        :param topic: Тема исследования
        :param queries: Дополнительные поисковые запросы (2–5)
        """
        token = ""
        if __request__ is not None:
            token = __request__.headers.get("authorization", "")
        if not token:
            return "Ошибка: нет токена пользователя"

        all_queries = [topic] + [q for q in queries if q and q != topic]
        all_queries = all_queries[:self.valves.max_queries]
        url = "http://127.0.0.1:8080/api/v1/retrieval/process/web/search"

        async def one_query(client, query):
            try:
                response = await client.post(
                    url, headers={"Authorization": token},
                    json={"queries": [query]},
                )
                if response.status_code != 200:
                    return []
                data = response.json()
                return data.get("items", []) if isinstance(data, dict) else []
            except Exception:
                return []

        async with httpx.AsyncClient(timeout=self.valves.timeout) as client:
            groups = await asyncio.gather(*[one_query(client, q) for q in all_queries])

        seen, lines = set(), [f"# Исследование: {topic}", ""]
        count = 0
        for group in groups:
            for item in group:
                link = item.get("link") or item.get("url") or ""
                if not link or link in seen:
                    continue
                seen.add(link)
                count += 1
                title = (item.get("title") or link)[:160]
                snippet = (item.get("snippet") or item.get("content") or "").replace("\n", " ")[:300]
                lines.append(f"{count}. [{title}]({link})")
                if snippet:
                    lines.append(f"   {snippet}")
                if count >= self.valves.max_results:
                    break
            if count >= self.valves.max_results:
                break

        if not lines[2:]:
            return f"По теме «{topic}» не найдено результатов. Попробуйте уточнить запрос."
        lines += ["", "Источники собраны через настроенный поисковый движок OmniSecretary."]
        return "\n".join(lines)
