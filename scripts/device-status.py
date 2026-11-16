#!/usr/bin/env python3
"""Снимок состояния Raspberry Pi для админ-панели портала.

Запускается по cron раз в минуту от root, пишет JSON в
${DATA_ROOT}/status/device.json. Портал только читает файл: docker-сокет
в него не монтируется, лишних контейнеров нет.

Запуск вручную: sudo python3 scripts/device-status.py
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import ssl
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

DATA_ROOT = Path(os.environ.get("DATA_ROOT", "/srv/omnisecretary"))
OUT = Path(os.environ.get("STATUS_FILE", str(DATA_ROOT / "status" / "device.json")))
PROJECT_DIR = Path(__file__).resolve().parent.parent

THROTTLE_BITS = {
    0: "under-voltage",
    1: "freq-capped",
    2: "throttled",
    3: "soft-temp-limit",
    16: "under-voltage",
    17: "freq-capped",
    18: "throttled",
    19: "soft-temp-limit",
}


def run(*args: str, timeout: int = 10) -> str:
    try:
        out = subprocess.run(args, capture_output=True, text=True,
                             timeout=timeout, check=False)
        return out.stdout.strip()
    except Exception:  # noqa: BLE001 — метрика не должна ломать снимок
        return ""


def read(path: str, default: str = "") -> str:
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return default


def temp_c() -> float | None:
    raw = read("/sys/class/thermal/thermal_zone0/temp")
    if not raw.isdigit():
        return None
    return round(int(raw) / 1000, 1)


def throttled() -> tuple[str, list[str], list[str]]:
    """Возвращает raw, текущие флаги (биты 0–3) и исторические (16–19)."""
    raw = run("vcgencmd", "get_throttled")
    value = raw.split("=", 1)[1] if "=" in raw else "0x0"
    try:
        flags = int(value, 16)
    except ValueError:
        flags = 0
    current = [THROTTLE_BITS[b] for b in range(4) if flags & (1 << b)]
    history = [THROTTLE_BITS[b] for b in range(16, 20) if flags & (1 << b)]
    return value, current, history


def freq_mhz() -> int | None:
    raw = read("/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq")
    return int(raw) // 1000 if raw.isdigit() else None


def memory() -> dict:
    info: dict[str, int] = {}
    for line in read("/proc/meminfo").splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1].isdigit():
            info[parts[0].rstrip(":")] = int(parts[1])
    mb = lambda kb: round(kb / 1024)  # noqa: E731
    total = info.get("MemTotal", 0)
    available = info.get("MemAvailable", 0)
    return {
        "total_mb": mb(total),
        "used_mb": mb(total - available),
        "available_mb": mb(available),
        "swap_total_mb": mb(info.get("SwapTotal", 0)),
        "swap_used_mb": mb(info.get("SwapTotal", 0) - info.get("SwapFree", 0)),
    }


def disks() -> dict:
    result = {}
    for label, path in (("/", "/"), ("/srv/omnisecretary", str(DATA_ROOT))):
        try:
            usage = shutil.disk_usage(path)
        except OSError:
            continue
        result[label] = {
            "total_gb": round(usage.total / 1024**3, 1),
            "used_gb": round(usage.used / 1024**3, 1),
            "free_gb": round(usage.free / 1024**3, 1),
            "pct": round(usage.used / usage.total * 100),
        }
    return result


def containers() -> dict:
    raw = run("docker", "ps", "-a", "--format",
              "{{.Names}}|{{.State}}|{{.Status}}", timeout=20)
    total = running = 0
    unhealthy: list[str] = []
    for line in raw.splitlines():
        parts = line.split("|")
        if len(parts) < 3:
            continue
        total += 1
        if parts[1] == "running":
            running += 1
        if "unhealthy" in parts[2]:
            unhealthy.append(parts[0])
    return {"total": total, "running": running, "unhealthy": unhealthy}


def cert_days() -> int | None:
    live = Path("/etc/letsencrypt/live")
    if not live.is_dir():
        return None
    best: int | None = None
    for cert in live.glob("*/cert.pem"):
        try:
            decoded = ssl._ssl._test_decode_cert(str(cert))  # noqa: SLF001
            expires = ssl.cert_time_to_seconds(decoded["notAfter"])
        except Exception:  # noqa: BLE001
            continue
        days = int((expires - time.time()) // 86400)
        best = days if best is None else min(best, days)
    return best


def backup_info() -> dict:
    root = DATA_ROOT / "backups"
    if not root.is_dir():
        return {"last": None, "size_mb": None}
    dirs = sorted((d for d in root.iterdir() if d.is_dir()),
                  key=lambda d: d.stat().st_mtime, reverse=True)
    if not dirs:
        return {"last": None, "size_mb": None}
    latest = dirs[0]
    size = sum(f.stat().st_size for f in latest.rglob("*") if f.is_file())
    return {"last": latest.name, "size_mb": round(size / 1024**2, 1)}


def uptime() -> str:
    raw = read("/proc/uptime").split()
    try:
        seconds = int(float(raw[0])) if raw else 0
    except (ValueError, IndexError):
        seconds = 0
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    if days:
        return f"{days} д {hours} ч {minutes} мин"
    return f"{hours} ч {minutes} мин"


def ufw_state() -> str:
    # В cron PATH короткий: /usr/sbin может отсутствовать.
    binary = "/usr/sbin/ufw" if Path("/usr/sbin/ufw").exists() else "ufw"
    raw = run(binary, "status", timeout=10)
    first = raw.splitlines()[0] if raw else ""
    return first.replace("Status: ", "") or "неизвестно"


def main() -> int:
    load1, load5, load15 = os.getloadavg()
    throttle_raw, throttle_flags, throttle_history = throttled()
    snapshot = {
        "time": datetime.now().astimezone().isoformat(timespec="seconds"),
        "model": read("/proc/device-tree/model").replace("\x00", ""),
        "kernel": platform.release(),
        "uptime": uptime(),
        "temp_c": temp_c(),
        "throttled": throttle_raw,
        "throttle_flags": throttle_flags,
        "throttle_history": throttle_history,
        "freq_mhz": freq_mhz(),
        "cores": os.cpu_count(),
        "load1": round(load1, 2),
        "load": f"{load1:.2f} / {load5:.2f} / {load15:.2f}",
        "mem": memory(),
        "disk": disks(),
        "containers": containers(),
        "cert_days": cert_days(),
        "backup": backup_info(),
        "ufw": ufw_state(),
        "project": str(PROJECT_DIR),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    tmp.chmod(0o644)
    os.replace(tmp, OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
