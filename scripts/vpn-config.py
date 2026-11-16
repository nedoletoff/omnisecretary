#!/usr/bin/env python3
"""Генерирует конфиг Xray-core из подписки VPN (vless).

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
# Xray умеет ws/xhttp/grpc/tcp; остальные транспорты пропускаем.
SUPPORTED_NET = {"tcp", "ws", "grpc", "http", "h2", "xhttp"}


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
    return {
        "name": urllib.parse.unquote(parts.fragment or ""),
        "host": parts.hostname or "",
        "port": parts.port or 443,
        "uuid": urllib.parse.unquote(parts.username or ""),
        "net": (query.get("type") or "tcp").lower(),
        "security": (query.get("security") or "none").lower(),
        "sni": query.get("sni") or "",
        "fp": query.get("fp") or "chrome",
        "pbk": query.get("pbk") or "",
        "sid": query.get("sid") or "",
        "path": query.get("path") or "/",
        "host_header": query.get("host") or "",
        "service": query.get("serviceName") or "",
        "mode": query.get("mode") or "auto",
        "flow": query.get("flow") or "",
        "alpn": [a for a in (query.get("alpn") or "").split(",") if a],
    }


def xray_outbound(item: dict, tag: str) -> dict:
    """VLESS-выход для Xray-core: ws, xhttp, grpc, tcp, http."""
    stream: dict = {"network": item["net"]}
    security = item["security"]
    if security == "reality":
        stream["security"] = "reality"
        stream["realitySettings"] = {
            "serverName": item["sni"],
            "fingerprint": item["fp"],
            "publicKey": item["pbk"],
            "shortId": item["sid"],
            "spiderX": "/",
        }
    elif security == "tls":
        stream["security"] = "tls"
        stream["tlsSettings"] = {
            "serverName": item["sni"] or item["host"],
            "fingerprint": item["fp"],
            **({"alpn": item["alpn"]} if item["alpn"] else {}),
        }
    else:
        stream["security"] = "none"

    if item["net"] == "ws":
        ws: dict = {"path": item["path"]}
        if item["host_header"]:
            ws["host"] = item["host_header"]
        stream["wsSettings"] = ws
    elif item["net"] == "xhttp":
        # Провайдер отдаёт mode=stream-one, но при потоке коротких
        # соединений он рвёт TLS (UNEXPECTED_EOF). В auto Xray сам
        # выбирает рабочий режим — проверено: 10/10 запросов без сбоев.
        xhttp = {"path": item["path"], "mode": "auto"}
        if item["host_header"]:
            xhttp["host"] = item["host_header"]
        stream["xhttpSettings"] = xhttp
    elif item["net"] == "grpc":
        stream["grpcSettings"] = {"serviceName": item["service"]}
    elif item["net"] in ("http", "h2"):
        stream["network"] = "http"
        stream["httpSettings"] = {"path": item["path"]}

    user: dict = {"id": item["uuid"], "encryption": "none"}
    if item["flow"]:
        user["flow"] = item["flow"]
    return {
        "tag": tag,
        "protocol": "vless",
        "settings": {"vnext": [{
            "address": item["host"],
            "port": item["port"],
            "users": [user],
        }]},
        "streamSettings": stream,
    }


def tcp_latency(host: str, port: int, timeout: float = 3.0) -> float | None:
    started = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return (time.monotonic() - started) * 1000
    except OSError:
        return None


def build_config(outbounds: list[dict]) -> dict:
    """Конфиг Xray-core: HTTP-прокси на 2080 и SOCKS5 на 2081.

    Провайдер отдаёт VLESS поверх xhttp+reality; sing-box такую связку
    не умеет, Xray — умеет. Несколько серверов объединены балансером
    leastPing: при отказе одного трафик уходит на живой.
    """
    tags = [item["tag"] for item in outbounds]
    return {
        "log": {"loglevel": "warning"},
        "dns": {
            "servers": ["https+local://1.1.1.1/dns-query"],
            "queryStrategy": "UseIPv4",
        },
        "inbounds": [
            {
                "tag": "http-in",
                "listen": "0.0.0.0",
                "port": 2080,
                "protocol": "http",
                "settings": {"allowTransparent": False},
            },
            {
                "tag": "socks-in",
                "listen": "0.0.0.0",
                "port": 2081,
                "protocol": "socks",
                "settings": {"auth": "noauth", "udp": True},
            },
        ],
        "outbounds": [
            *outbounds,
            {"tag": "direct", "protocol": "freedom"},
            {"tag": "block", "protocol": "blackhole"},
        ],
        "observatory": {
            "subjectSelector": tags,
            "probeUrl": "https://www.gstatic.com/generate_204",
            "probeInterval": "3m",
            "enableConcurrency": True,
        },
        "routing": {
            "domainStrategy": "AsIs",
            "balancers": [{
                "tag": "proxy",
                "selector": tags,
                "strategy": {"type": "leastPing"},
            }],
            "rules": [
                {"type": "field", "ip": ["geoip:private"], "outboundTag": "direct"},
            ],
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
    if not measured and not args.server:
        # Все Auto легли — берём обычные серверы (без AI, белых списков,
        # торрентов и YouTube), иначе Telegram останется без связи.
        fallback = []
        for line in text.splitlines():
            if not line.startswith("vless://"):
                continue
            item = parse_vless(line.strip())
            if item["net"] not in SUPPORTED_NET:
                continue
            if any(word in item["name"].lower() for word in EXCLUDE):
                continue
            fallback.append(item)
        for item in fallback:
            latency = tcp_latency(item["host"], item["port"])
            if latency is not None:
                measured.append((latency, item))
        if measured:
            print("[warn] Auto-серверы недоступны — использую обычные",
                  file=sys.stderr)
    if not measured:
        print("[fail] ни один сервер не отвечает по TCP", file=sys.stderr)
        return 1
    measured.sort(key=lambda pair: pair[0])
    # Берём до 6 самых быстрых: urltest переключится, если сервер отвалится.
    selected = measured[:6]
    outbounds = []
    for index, (latency, item) in enumerate(selected, start=1):
        outbounds.append(xray_outbound(item, f"s{index}"))
        print(f"  s{index}: {item['name']} ({latency:.0f} мс, {item['net']})")

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
