#!/usr/bin/env python3
"""MCP-сервер файлов: общий каталог ${DATA_ROOT}/shared.

Даёт ассистенту читать и складывать файлы, не открывая весь хост.
Все пути ограничены SHARED_DIR; выход за пределы запрещён.
"""

from __future__ import annotations

import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

SHARED_DIR = Path(os.environ.get("SHARED_DIR", "/shared")).resolve()
MAX_READ = 20000

mcp = FastMCP(
    "files",
    host="0.0.0.0",
    port=int(os.environ.get("MCP_PORT", "8000")),
    streamable_http_path="/mcp",
    stateless_http=True,
    instructions=(
        "Общий каталог файлов. Сначала files_list или files_search, "
        "потом files_read одного файла. Запись — files_write."
    ),
)


def _safe(relative: str) -> Path:
    target = (SHARED_DIR / relative.lstrip("/")).resolve()
    if SHARED_DIR not in target.parents and target != SHARED_DIR:
        raise ValueError("путь вне общего каталога")
    return target


@mcp.tool()
def files_list(folder: str = "", limit: int = 100) -> str:
    """Список файлов и папок (пути и размеры, без содержимого)."""
    try:
        base = _safe(folder) if folder else SHARED_DIR
    except ValueError as exc:
        return f"ошибка: {exc}"
    if not base.is_dir():
        return f"нет папки: {folder}"
    lines = []
    for entry in sorted(base.iterdir())[: max(1, min(int(limit), 300))]:
        rel = entry.relative_to(SHARED_DIR)
        if entry.is_dir():
            lines.append(f"{rel}/")
        else:
            lines.append(f"{rel} ({entry.stat().st_size} Б)")
    return "\n".join(lines) or "пусто"


@mcp.tool()
def files_read(path: str, max_chars: int = 6000) -> str:
    """Прочитать текстовый файл (обрезается, чтобы не забивать контекст)."""
    try:
        target = _safe(path)
    except ValueError as exc:
        return f"ошибка: {exc}"
    if not target.is_file():
        return f"нет файла: {path}"
    text = target.read_text(encoding="utf-8", errors="replace")
    limit = max(500, min(int(max_chars), MAX_READ))
    return text[:limit] + ("" if len(text) <= limit else f"\n…(ещё {len(text) - limit} символов)")


@mcp.tool()
def files_search(query: str, folder: str = "", limit: int = 10) -> str:
    """Поиск подстроки по текстовым файлам. Возвращает путь:строку и фрагмент."""
    try:
        base = _safe(folder) if folder else SHARED_DIR
    except ValueError as exc:
        return f"ошибка: {exc}"
    needle = query.lower()
    found = []
    for path in sorted(base.rglob("*")):
        if not path.is_file() or path.stat().st_size > 2_000_000:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for number, line in enumerate(text.splitlines(), 1):
            if needle in line.lower():
                found.append(f"{path.relative_to(SHARED_DIR)}:{number}: {line.strip()[:160]}")
                break
        if len(found) >= max(1, min(int(limit), 50)):
            break
    return "\n".join(found) or "ничего не найдено"


@mcp.tool()
def files_write(path: str, content: str, append: bool = False) -> str:
    """Записать или дополнить текстовый файл в общем каталоге."""
    try:
        target = _safe(path)
    except ValueError as exc:
        return f"ошибка: {exc}"
    target.parent.mkdir(parents=True, exist_ok=True)
    mode = "a" if append else "w"
    with target.open(mode, encoding="utf-8") as handle:
        handle.write(content)
    return f"записано: {target.relative_to(SHARED_DIR)} ({target.stat().st_size} Б)"


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
