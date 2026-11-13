"""Админы компьютерийг дотоод сүлжээнээс автоматаар олох (UDP broadcast, зөвхөн Python стандарт сан).

Админы компьютер дээрх програм UDP 48765-ыг сонсож, "хаана байна?" гэсэн асуултад хавтасны хаяг,
нэвтрэх эрхээр хариулна. Ажилтны програм share.json байхгүй / буруу / хавтас холбогдохгүй үед асуултаа цацаж,
хариултыг хадгална — ажилтанд юу ч оруулах шаардлагагүй.
"""
from __future__ import annotations

import ipaddress
import json
import logging
import socket
import threading
import time

from . import APP_NAME

log = logging.getLogger(__name__)

PORT = 48765
MAGIC = b"AJLIIN_BUTEEMJ_WHERE/1"
MAX_PACKET = 2048


def _is_local(ip: str) -> bool:
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return a.is_private or a.is_loopback or a.is_link_local


def local_ipv4() -> list[str]:
    ips = set()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))  # пакет илгээхгүй — зөвхөн гарах интерфэйсийг сонгуулна
            ips.add(s.getsockname()[0])
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    return sorted(ip for ip in ips if not ip.startswith("127."))


def _targets() -> list[str]:
    out = ["255.255.255.255"]
    for ip in local_ipv4():
        parts = ip.split(".")
        if len(parts) == 4:
            out.append(".".join(parts[:3] + ["255"]))  # /24 — олон адаптертай Windows-д хэрэгтэй
    out.append("127.0.0.1")
    return list(dict.fromkeys(out))


class Responder:
    """Админы компьютер дээр: асуултад хавтасны мэдээллээр хариулна."""

    def __init__(self, info: dict, port: int = PORT):
        self.reply = json.dumps({
            "app": APP_NAME, "v": 1, "host": socket.gethostname(),
            "share": info.get("share"), "user": info.get("user"), "password": info.get("password"),
        }).encode("utf-8")
        self.port = port
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="discovery", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.bind(("0.0.0.0", self.port))
        except OSError as exc:
            log.warning("Хайлтын хариулагч эхэлсэнгүй (UDP %d): %s", self.port, exc)
            return
        sock.settimeout(1.0)
        log.info("Ажилтнуудын програм энэ компьютерийг автоматаар олно (UDP %d)", self.port)
        with sock:
            while not self._stop.is_set():
                try:
                    data, addr = sock.recvfrom(MAX_PACKET)
                except socket.timeout:
                    continue
                except OSError:
                    if self._stop.wait(1):
                        break
                    continue
                if data.strip() == MAGIC and _is_local(addr[0]):
                    try:
                        sock.sendto(self.reply, addr)
                    except OSError:
                        pass


def discover(timeout: float = 3.0, port: int = PORT) -> dict | None:
    """Админы компьютерийг хайна. Олдвол share.json-ийн бүтэцтэй dict (+ "ip"), үгүй бол None."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    except OSError:
        return None
    with sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        try:
            sock.bind(("0.0.0.0", 0))
        except OSError:
            return None
        for target in _targets():
            try:
                sock.sendto(MAGIC, (target, port))
            except OSError:
                pass
        deadline = time.monotonic() + timeout
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                return None
            sock.settimeout(left)
            try:
                data, addr = sock.recvfrom(MAX_PACKET)
            except (socket.timeout, OSError):
                return None
            try:
                d = json.loads(data.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                continue
            if isinstance(d, dict) and d.get("app") == APP_NAME and d.get("share") and _is_local(addr[0]):
                log.info("Админы компьютер олдлоо: %s (%s)", d.get("host"), addr[0])
                return {"share": d["share"], "user": d.get("user"), "password": d.get("password"),
                        "host": d.get("host"), "ip": addr[0]}
