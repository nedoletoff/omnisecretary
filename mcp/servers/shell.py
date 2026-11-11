#!/usr/bin/env python3
"""MCP-сервер «песочница»: ограниченный запуск команд.

Работает внутри отдельного контейнера с каталогом /workspace.
Ни Docker-сокета, ни доступа к хосту: даже «плохая» команда видит только
песочницу. Разрешён список безопасных программ, метасимволы оболочки
запрещены, пути — только внутри /workspace и /tmp.

Опасные операции (sudo, apt, systemctl, rm -rf, dd, mkfs, reboot)
намеренно недоступны: такие действия владелец выполняет вручную.
"""

from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path

from mcp.server.fastmcp import FastMCP

WORKSPACE = Path(os.environ.get("SANDBOX_DIR", "/workspace")).resolve()
TIMEOUT = int(os.environ.get("SANDBOX_TIMEOUT", "60"))

ALLOWED = {
    "ls", "cat", "head", "tail", "wc", "grep", "find", "pwd", "echo",
    "python3", "python", "pip", "node", "npm", "git", "date", "uname",
    "df", "free", "ps", "which", "mkdir", "touch", "cp", "mv",
}
FORBIDDEN_TOKENS = {
    "sudo", "su", "apt", "apt-get", "dpkg", "systemctl", "service",
    "rm", "rmdir", "dd", "mkfs", "fdisk", "parted", "mount", "umount",
    "shutdown", "reboot", "poweroff", "docker", "podman", "kubectl",
    "chmod", "chown", "chroot", "nsenter", "iptables", "nft",
}
METACHARS = set(";|&><`$\n")

mcp = FastMCP(
    "sandbox",
    host="0.0.0.0",
    port=int(os.environ.get("MCP_PORT", "8000")),
    streamable_http_path="/mcp",
    stateless_http=True,
    instructions=(
        "Песочница: можно запускать код и прототипы в /workspace. "
        "Разрешены базовые команды и python3/node/git. Опасные операции запрещены."
    ),
)


def _check_path(token: str) -> None:
    if token.startswith("/"):
        target = Path(token).resolve()
        if WORKSPACE not in target.parents and target != WORKSPACE and not str(target).startswith("/tmp"):
            raise ValueError(f"путь вне песочницы: {token}")


@mcp.tool()
def sandbox_run(command: str, timeout: int = 30) -> str:
    """Выполнить команду в песочнице. Возвращает stdout/stderr и код возврата."""
    if any(char in command for char in METACHARS):
        return "запрещены метасимволы оболочки (; | & > < ` $)"
    try:
        parts = shlex.split(command)
    except ValueError as exc:
        return f"не удалось разобрать команду: {exc}"
    if not parts:
        return "пустая команда"
    program = os.path.basename(parts[0])
    if program in FORBIDDEN_TOKENS or parts[0] in FORBIDDEN_TOKENS:
        return f"команда {program} запрещена (опасная операция)"
    if program not in ALLOWED:
        return f"команда {program} не разрешена. Разрешены: {', '.join(sorted(ALLOWED))}"
    try:
        for token in parts[1:]:
            if not token.startswith("-"):
                _check_path(token)
    except ValueError as exc:
        return f"ошибка: {exc}"
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    try:
        result = subprocess.run(
            parts, cwd=WORKSPACE, capture_output=True, text=True,
            timeout=max(1, min(int(timeout), TIMEOUT)),
        )
    except subprocess.TimeoutExpired:
        return f"таймаут {timeout} с"
    output = (result.stdout or "") + (("\n[stderr]\n" + result.stderr) if result.stderr else "")
    return f"код {result.returncode}\n{output[:8000]}"


@mcp.tool()
def sandbox_write(path: str, content: str) -> str:
    """Создать или заменить файл в песочнице (например, прототип скрипта)."""
    target = (WORKSPACE / path.lstrip("/")).resolve()
    if WORKSPACE not in target.parents and target != WORKSPACE:
        return "ошибка: путь вне песочницы"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return f"записано: {target.relative_to(WORKSPACE)}"


@mcp.tool()
def sandbox_read(path: str, max_chars: int = 6000) -> str:
    """Прочитать файл из песочницы."""
    target = (WORKSPACE / path.lstrip("/")).resolve()
    if WORKSPACE not in target.parents and target != WORKSPACE:
        return "ошибка: путь вне песочницы"
    if not target.is_file():
        return f"нет файла: {path}"
    text = target.read_text(encoding="utf-8", errors="replace")
    limit = max(500, min(int(max_chars), 20000))
    return text[:limit] + ("" if len(text) <= limit else f"\n…(ещё {len(text) - limit})")


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
