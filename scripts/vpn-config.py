#!/usr/bin/env python3
"""Генерирует конфиг sing-box из подписки VPN (vless).

Правила выбора сервера:
- только обычные «Auto» серверы;
- исключаются нейросети (AI), белые списки, торренты, YouTube-оптимизации;
- из оставшихся выбирается самый быстрый по TCP-задержке.

Секреты не печатаются: ссылка на подписку и UUID остаются в .env и
${DATA_ROOT}/vpn/config.json (права 600).
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import socket
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXCLUDE = ("ai", "нейро", "белые", "whitelist", "white list", "торрент",
           "torrent", "youtube", "ютуб", "госуслуги", "gosuslugi")
# sing-box не умеет XHTTP; такие Auto-серверы пропускаем.
SUPPORTED_NET = {"tcp", "ws", "grpc", "http", "h2"}


def load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def fetch_subscription(url: str) -> str:
    request = urllib.request.Request(url, headers={
        "User-Agent": "Happ/1.0",
        "Accept-Encoding": "identity",
    })
    with urllib.request.urlopen(request, timeout=30) as response:
        raw = response.read()
    text = raw.decode("utf-8", "replace").strip()
    if "://" not in text:
        padded = text + "=" * (-len(text) % 4)
        text = base64.b64decode(padded).decode("utf-8", "replace")
    return text


def is_regular_auto(name: str) -> bool:
    low = name.lower()
    if any(word in low for word in EXCLUDE):
        return False
    return low.startswith("auto") or "авто" in low


def parse_vless(link: str) -> dict:
    parts = urllib.parse.urlsplit(link)
    query = dict(urllib.parse.parse_qsl(parts.query))
    name = urllib.parse.unquote(parts.fragment or "")
    host = parts.hostname or ""
    port = parts.port or 443
    uuid = urllib.parse.unquote(parts.username or "")

    tls: dict = {}
    security = (query.get("security") or "none").lower()
    if security in ("tls", "reality"):
        tls = {"enabled": True, "server_name": query.get("sni") or host}
        fingerprint = query.get("fp") or "chrome"
        tls["utls"] = {"enabled": True, "fingerprint": fingerprint}
        if security == "reality":
            tls["reality"] = {
                "enabled": True,
                "public_key": query.get("pbk", ""),
                "short_id": query.get("sid", ""),
            }
        alpn = query.get("alpn")
        if alpn:
            tls["alpn"] = [item for item in alpn.split(",") if item]

    transport: dict | None = None
    net = (query.get("type") or "tcp").lower()
    if net == "ws":
        transport = {"type": "ws", "path": query.get("path") or "/"}
        host_header = query.get("host")
        if host_header:
            transport["headers"] = {"Host": host_header}
    elif net == "grpc":
        transport = {"type": "grpc", "service_name": query.get("serviceName") or ""}
    elif net in ("http", "h2"):
        transport = {"type": "http", "path": query.get("path") or "/"}

    outbound = {
        "type": "vless",
        "tag": "proxy",
        "server": host,
        "server_port": port,
        "uuid": uuid,
    }
    if query.get("flow"):
        outbound["flow"] = query["flow"]
    if tls:
        outbound["tls"] = tls
    if transport:
        outbound["transport"] = transport
    return {"name": name, "host": host, "port": port, "net": net,
            "outbound": outbound}


def tcp_latency(host: str, port: int, timeout: float = 3.0) -> float | None:
    started = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return (time.monotonic() - started) * 1000
    except OSError:
        return None


def build_config(outbounds: list[dict]) -> dict:
    """Mixed-прокси (HTTP CONNECT + SOCKS5) без TUN.

    Приложения ходят через прокси по env HTTPS_PROXY/HTTP_PROXY. Это
    надёжнее TUN: DNS резолвится на стороне VPN, локальные сервисы
    (postgres, qdrant, open-webui) остаются напрямую, обновления хоста
    (apt, docker pull) VPN не затрагивают.
    """
    return {
        "log": {"level": "warn", "timestamp": True},
        "dns": {
            "servers": [
                {"type": "https", "tag": "remote", "server": "1.1.1.1",
                 "detour": "proxy"},
                {"type": "local", "tag": "local"},
            ],
            "final": "remote",
            "strategy": "prefer_ipv4",
        },
        "inbounds": [{
            "type": "mixed",
            "tag": "mixed-in",
            "listen": "0.0.0.0",
            "listen_port": 2080,
        }],
        # Несколько серверов + автопереключение: если один отваливается,
        # sing-box сам переходит на следующий по доступности.
        "outbounds": [
            *outbounds,
            {
                "type": "urltest",
                "tag": "proxy",
                "outbounds": [item["tag"] for item in outbounds],
                "url": "https://www.gstatic.com/generate_204",
                "interval": "3m",
                "tolerance": 50,
            },
            {"type": "direct", "tag": "direct"},
        ],
        "route": {
            "final": "proxy",
            "default_domain_resolver": "local",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", default=str(ROOT / ".env"))
    parser.add_argument("--out", default="")
    parser.add_argument("--if-missing", action="store_true",
                        help="не перегенерировать, если конфиг уже есть")
    parser.add_argument("--list", action="store_true", help="только показать кандидатов")
    parser.add_argument("--server", default=os.environ.get("VPN_SERVER", ""),
                        help="точное имя сервера из подписки")
    args = parser.parse_args()

    env = load_env(Path(args.env))
    data_root = Path(env.get("DATA_ROOT", "/srv/omnisecretary"))
    out_path = Path(args.out or data_root / "vpn" / "config.json")

    if args.if_missing and out_path.exists() and out_path.stat().st_size > 0:
        print(f"конфиг уже есть: {out_path}")
        return 0

    url = env.get("VPN_KEY", "").strip()
    if not url:
        print("[skip] VPN_KEY не задан — VPN-контейнер не настроен", file=sys.stderr)
        return 0

    try:
        text = fetch_subscription(url)
    except Exception as error:  # noqa: BLE001
        print(f"[fail] подписка недоступна: {type(error).__name__}", file=sys.stderr)
        return 1

    candidates = []
    for line in text.splitlines():
        if not line.startswith("vless://"):
            continue
        item = parse_vless(line.strip())
        if item["net"] not in SUPPORTED_NET:
            continue
        if args.server:
            if item["name"] != args.server:
                continue
        elif not is_regular_auto(item["name"]):
            continue
        candidates.append(item)

    if not candidates:
        print("[fail] в подписке нет подходящих Auto-серверов", file=sys.stderr)
        return 1

    if args.list:
        for item in candidates:
            print(f"{item['name']} — {item['host']}:{item['port']} ({item['net']})")
        return 0

    measured = []
    for item in candidates:
        latency = tcp_latency(item["host"], item["port"])
        if latency is not None:
            measured.append((latency, item))
    if not measured:
        print("[fail] ни один Auto-сервер не отвечает по TCP", file=sys.stderr)
        return 1
    measured.sort(key=lambda pair: pair[0])
    # Берём до 6 самых быстрых: urltest переключится, если сервер отвалится.
    selected = measured[:6]
    outbounds = []
    for index, (latency, item) in enumerate(selected, start=1):
        outbound = dict(item["outbound"])
        outbound["tag"] = f"s{index}"
        outbounds.append(outbound)
        print(f"  s{index}: {item['name']} ({latency:.0f} мс)")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(build_config(outbounds), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    out_path.chmod(0o600)
    print(f"[ok] серверов в пуле: {len(outbounds)} → {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
