"""HTTPS/SSL туслах, дотоод сүлжээний IP.

certifi-ийн гэрчилгээний багцыг ашиглана — компьютер бүрийн гэрчилгээний тохиргооноос хамаарахгүй.
Зөвхөн стандарт сан (build.py ч импортолно).
"""
from __future__ import annotations

import ipaddress
import json
import socket
import ssl
import urllib.error
import urllib.request

_ctx: ssl.SSLContext | None = None
MAX_HUB_IPS = 4


def lan_ips(values) -> list[str]:
    """Дотоод сүлжээний IPv4 хаягуудыг шүүнэ (давхардалгүй, дараалал хэвээр, ≤ MAX_HUB_IPS)."""
    out: list[str] = []
    for v in values if isinstance(values, (list, tuple)) else []:
        try:
            a = ipaddress.ip_address(str(v).strip())
        except ValueError:
            continue
        if a.version == 4 and a.is_private and not a.is_loopback and not a.is_link_local and str(a) not in out:
            out.append(str(a))
    return out[:MAX_HUB_IPS]


def local_ips() -> list[str]:
    """Энэ компьютерийн дотоод сүлжээний IPv4 хаягууд, сүлжээ рүү гардаг үндсэн нь эхэндээ."""
    found: list[str] = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))  # UDP: пакет илгээхгүй, аль адаптераар гарахыг л тодорхойлно
            found.append(s.getsockname()[0])
    except OSError:
        pass
    try:
        found += [ai[4][0] for ai in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)]
    except OSError:
        pass
    return lan_ips(found)


def ssl_context() -> ssl.SSLContext:
    global _ctx
    if _ctx is None:
        try:
            import certifi

            _ctx = ssl.create_default_context(cafile=certifi.where())
        except ImportError:
            _ctx = ssl.create_default_context()
    return _ctx


def post_json(url: str, payload: dict | bytes, headers: dict, timeout: float = 20) -> bytes:
    """POST хийж хариуг буцаана. HTTP алдааны үед серверийн мессежтэй RuntimeError."""
    data = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    hdrs = {"Content-Type": "application/json", **headers}
    req = urllib.request.Request(url, data=data, headers=hdrs, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ssl_context()) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:300]
        raise RuntimeError(f"HTTP {exc.code}: {body}") from None
