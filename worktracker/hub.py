"""Админы компьютер дээрх жижиг сервер (зөвхөн Python стандарт сан, дотоод сүлжээнд).

Ажилтны програмууд өгөгдлөө хуваалцсан хавтсаар биш, энд HTTP-ээр илгээнэ. Ингэснээр:
  * exe дотор нууц үг байхгүй — задалсан ч бусдын өгөгдөлд хүрэхгүй;
  * ажилтан бүр анх холбогдохдоо ХУВИЙН түлхүүр авна (devices.json), дараа нь хүсэлт бүрт HMAC гарын үсэг зурна —
    түлхүүр сүлжээгээр дахин явахгүй;
  * сервер ажилтанд зөвхөн ӨӨРИЙН өгөгдлийг бичихийг зөвшөөрнө, бусдынхыг уншуулахгүй.

Өгөгдөл: share.ShareClient-ээр C:\\AjliinButeemjData-д (хуваалцаагүй) бичигдэнэ — самбар, Telegram өмнөх шигээ уншина.

Хүсэлтүүд:
  GET  /api/ping                    — сервер байна уу
  POST /api/register                — {uid, name, hostname} → {secret}  (тухайн uid анх удаа л)
  POST /api/push        (гарын үсэг) — share._wt_push-тэй ижил талбарууд
  GET  /api/settings    (гарын үсэг) — админы тохиргоо
  GET  /api/update/version.json      — шинэ хувилбарын мэдээлэл
  GET  /api/update/<файл>            — шинэ exe (SHA-256-аар шалгагдана)
Гарын үсэг: X-Uid, X-Ts (unix сек), X-Sig = hex(HMAC-SHA256(secret, "{ts}\\n{METHOD}\\n{path}\\n" + body)).
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import logging
import re
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import APP_NAME
from .share import DEVICES_FILE, ShareClient, ShareError, read_json, utcnow_iso, write_json

log = logging.getLogger(__name__)

MAX_BODY = 512 * 1024
MAX_SKEW_SEC = 300
UID_RE = re.compile(r"^[A-Za-z0-9._@-]{1,80}$")
UPDATE_FILE_RE = re.compile(r"^[A-Za-z0-9._-]{1,80}$")
REGISTER_LIMIT_PER_HOUR = 30


def sign(secret: str, ts: str, method: str, path: str, body: bytes) -> str:
    msg = f"{ts}\n{method.upper()}\n{path}\n".encode("utf-8") + body
    return hmac.new(bytes.fromhex(secret), msg, hashlib.sha256).hexdigest()


def _is_local(ip: str) -> bool:
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return a.is_private or a.is_loopback or a.is_link_local


class HubError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class Hub:
    """Админы програм (app.py) эхлүүлдэг. root = C:\\AjliinButeemjData (админы локал хавтас)."""

    def __init__(self, root: Path, port: int):
        self.root = Path(root)
        self.port = port
        self._lock = threading.RLock()
        self._clients: dict[str, ShareClient] = {}
        self._registrations: dict[str, list[float]] = {}
        self._httpd: ThreadingHTTPServer | None = None

    # ------------------------------------------------------------------ асаах / зогсоох
    def start(self) -> bool:
        hub = self

        class Handler(_Handler):
            pass

        Handler.hub = hub
        try:
            self._httpd = ThreadingHTTPServer(("0.0.0.0", self.port), Handler)
        except OSError as exc:
            log.warning("Сервер эхэлсэнгүй (TCP %d): %s", self.port, exc)
            return False
        self._httpd.daemon_threads = True
        threading.Thread(target=self._httpd.serve_forever, name="hub", daemon=True).start()
        log.info("Сервер ажиллаж байна: TCP %d (ажилтнуудын програм энд өгөгдлөө илгээнэ)", self.port)
        return True

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None

    # ------------------------------------------------------------------ төхөөрөмж
    def _devices(self) -> dict:
        return read_json(self.root / DEVICES_FILE, {}) or {}

    def register(self, ip: str, body: dict) -> dict:
        uid = str(body.get("uid") or "")
        if not UID_RE.match(uid):
            raise HubError(400, "invalid uid")
        with self._lock:
            now = time.time()
            recent = [t for t in self._registrations.get(ip, []) if now - t < 3600]
            if len(recent) >= REGISTER_LIMIT_PER_HOUR:
                raise HubError(429, "rate limit")
            self._registrations[ip] = recent + [now]
            if uid in (ShareClient({"local_path": str(self.root)}, uid).company()["removed"]):
                raise HubError(403, "removed by admin")
            devices = self._devices()
            if uid in devices:
                raise HubError(409, "already registered")
            secret = secrets.token_hex(32)
            devices[uid] = {"secret": secret, "registered_at": utcnow_iso(), "ip": ip,
                            "name": str(body.get("name") or "")[:100], "hostname": str(body.get("hostname") or "")[:100]}
            write_json(self.root / DEVICES_FILE, devices)
        log.info("Шинэ төхөөрөмж бүртгэгдлээ: %s (%s)", uid, ip)
        return {"secret": secret}

    def verify(self, method: str, path: str, headers, body: bytes) -> str:
        uid, ts, sig = headers.get("X-Uid", ""), headers.get("X-Ts", ""), headers.get("X-Sig", "")
        if not UID_RE.match(uid) or not ts.isdigit() or not sig:
            raise HubError(401, "unsigned request")
        if abs(time.time() - int(ts)) > MAX_SKEW_SEC:
            raise HubError(401, "clock skew")
        dev = self._devices().get(uid)
        if not dev:
            raise HubError(401, "unknown device")
        if not hmac.compare_digest(sign(dev["secret"], ts, method, path, body), sig):
            raise HubError(401, "bad signature")
        return uid

    def _client(self, uid: str, name: str) -> ShareClient:
        with self._lock:
            c = self._clients.get(uid)
            if c is None:
                c = self._clients[uid] = ShareClient({"local_path": str(self.root)}, uid, name)
            elif name and name != c.name:
                c.set_name(name)
            return c

    # ------------------------------------------------------------------ үйлдлүүд
    def push(self, uid: str, params: dict) -> dict:
        c = self._client(uid, str(params.get("p_name") or "")[:100])
        try:
            return {"result": c.rpc("wt_push", params)}
        except ShareError as exc:
            status = 403 if "removed" in str(exc) else 400
            raise HubError(status, str(exc)) from None

    def settings(self, uid: str) -> dict:
        c = ShareClient({"local_path": str(self.root)}, uid).company()
        return {"settings": c["settings"], "removed": uid in c["removed"]}

    def update_file(self, name: str) -> Path:
        if not UPDATE_FILE_RE.match(name) or name.startswith("."):
            raise HubError(404, "not found")
        p = self.root / "update" / name
        if not p.is_file():
            raise HubError(404, "not found")
        return p


class _Handler(BaseHTTPRequestHandler):
    hub: Hub
    server_version = "AjliinButeemj"

    def log_message(self, fmt, *args):  # noqa: D401 — стандарт логийг хаана
        log.debug("%s %s", self.client_address[0], fmt % args)

    def _send(self, status: int, payload) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> bytes:
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY:
            raise HubError(413, "too large")
        return self.rfile.read(n) if n else b""

    def _json(self, raw: bytes) -> dict:
        try:
            d = json.loads(raw.decode("utf-8")) if raw else {}
        except (ValueError, UnicodeDecodeError):
            raise HubError(400, "invalid json") from None
        if not isinstance(d, dict):
            raise HubError(400, "invalid json")
        return d

    def _handle(self, method: str) -> None:
        try:
            if not _is_local(self.client_address[0]):
                raise HubError(403, "local network only")
            path = self.path.split("?", 1)[0]
            raw = self._body() if method == "POST" else b""
            if method == "GET" and path == "/api/ping":
                return self._send(200, {"app": APP_NAME, "ok": True})
            if method == "POST" and path == "/api/register":
                return self._send(200, self.hub.register(self.client_address[0], self._json(raw)))
            if method == "GET" and path.startswith("/api/update/"):
                return self._file(self.hub.update_file(path[len("/api/update/"):]))
            uid = self.hub.verify(method, path, self.headers, raw)
            if method == "POST" and path == "/api/push":
                return self._send(200, self.hub.push(uid, self._json(raw)))
            if method == "GET" and path == "/api/settings":
                return self._send(200, self.hub.settings(uid))
            raise HubError(404, "not found")
        except HubError as exc:
            self._send(exc.status, {"message": str(exc)})
        except Exception:  # noqa: BLE001
            log.exception("Серверийн алдаа: %s %s", method, self.path)
            self._send(500, {"message": "server error"})

    def _file(self, p: Path) -> None:
        size = p.stat().st_size
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(size))
        self.end_headers()
        with p.open("rb") as f:
            while True:
                chunk = f.read(256 * 1024)
                if not chunk:
                    break
                self.wfile.write(chunk)

    def do_GET(self):  # noqa: N802
        self._handle("GET")

    def do_POST(self):  # noqa: N802
        self._handle("POST")
