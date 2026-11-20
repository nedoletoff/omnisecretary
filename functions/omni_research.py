"""
title: Исследование и размышление
author: OmniSecretary
version: 0.1.0
description: Многошаговый веб-поиск с отчётом и источниками
"""

import asyncio
import httpx
from pydantic import BaseModel, Field

SELF = "http://127.0.0.1:8080"


class Tools:
    class Valves(BaseModel):
        max_queries: int = Field(default=5, ge=1, le=8)
        max_sources: int = Field(default=15, ge=3, le=40)
        timeout: int = Field(default=45, ge=10, le=120)

    def __init__(self):
        self.valves = self.Valves()

    def _auth(self, request=None):
        if request is None:
            raise Exception("Нет контекста запроса")
        auth = request.headers.get("authorization", "")
        if not auth.lower().startswith("bearer "):
            raise Exception("Нужна авторизация Open WebUI")
        return {"Authorization": auth, "Content-Type": "application/json"}

    async def research(self, topic: str, queries: list[str],
                       __request__=None) -> str:
        """Провести исследование: несколько поисковых запросов и сжатый отчёт.

        :param topic: Тема исследования
        :param queries: Список уточняющих поисковых запросов
        :return: Markdown-отчёт со ссылками на источники
        """
        auth = self._auth(__request__)
        queries = [q.strip() for q in queries if q and q.strip()][:self.valves.max_queries]
        if not queries:
            queries = [topic]

        async def one(query):
            try:
                async with httpx.AsyncClient(timeout=self.valves.timeout) as client:
                    r = await client.post(
                        SELF + "/api/v1/retrieval/process/web/search",
                        headers=auth, json={"queries": [query]})
                if r.status_code >= 400:
                    return [{"title": "Ошибка поиска", "link": "",
                             "snippet": str(r.status_code) + " " + r.text[:100]}]
                data = r.json()
                if isinstance(data, dict):
                    return data.get("result") or data.get("data") or []
                return data if isinstance(data, list) else []
            except Exception as exc:
                return [{"title": "Ошибка поиска", "link": "",
                         "snippet": str(exc)[:120]}]

        groups = await asyncio.gather(*[one(q) for q in queries])
        seen = set()
        lines = [f"# Исследование: {topic}", ""]
        for query, group in zip(queries, groups):
            lines.extend([f"## {query}", ""])
            count = 0
            for item in group:
                if not isinstance(item, dict):
                    continue
                link = str(item.get("link") or "").strip()
                title = str(item.get("title") or link or "Источник").strip()
                snippet = " ".join(str(item.get("snippet") or "").split())
                key = link or title
                if not key or key in seen:
                    continue
                seen.add(key)
                lines.append(f"- [{title}]({link}) — {snippet[:300]}" if link
                             else f"- {title} — {snippet[:300]}")
                count += 1
                if count >= 5:
                    break
            if count == 0:
                lines.append("- Ничего не найдено")
            lines.append("")
        lines.extend(["---", f"Всего уникальных источников: {len(seen)}.",
                      "Проверяйте факты по первоисточникам."])
        return "\n".join(lines)[:12000]
