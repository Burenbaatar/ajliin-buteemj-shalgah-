"""Ажилтны програм → админы компьютерийн сервер (hub.py). Зөвхөн Python стандарт сан.

Анх холбогдохдоо хувийн түлхүүр авч `%APPDATA%\\AjliinButeemj\\device.json`-д хадгална, дараа нь хүсэлт бүрт
HMAC гарын үсэг зурна. exe дотор нууц үг байхгүй — зөвхөн админы компьютерийн нэр, порт (hub.json).
cloud.py-д share.ShareClient-тэй ИЖИЛ интерфэйстэй: available(), select("wt_company"), rpc("wt_push"), set_name().
"""
from __future__ import annotations

import getpass
import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request

from . import __version__
from .config import data_dir
from .hub import sign
from .share import NetworkError, ShareError

log = logging.getLogger(__name__)

DEVICE_FILE = "device.json"


class HubClient:
    def __init__(self, hub: dict, uid: str, name: str = ""):
        self.host, self.port = str(hub["host"]), int(hub["port"])
        self.base = f"http://{self.host}:{self.port}"
        self.user_id = uid
        self.name = name
        self.root = None  # хавтасгүй (updater, selftest нь үүгээр ялгана)
        self.is_admin = False
        self._lock = threading.Lock()
        self._secret: str | None = None
        self._load_secret()

    # ------------------------------------------------------------------ түлхүүр
    def _device_path(self):
        return data_dir() / DEVICE_FILE

    def _load_secret(self) -> None:
        try:
            d = json.loads(self._device_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if d.get("uid") == self.user_id and d.get("host", "").lower() == self.host.lower() and d.get("secret"):
            self._secret = d["secret"]

    def _save_secret(self, secret: str | None) -> None:
        self._secret = secret
        p = self._device_path()
        if secret is None:
            try:
                p.unlink()
            except FileNotFoundError:
                pass
            return
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps({"uid": self.user_id, "host": self.host, "secret": secret}), encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, p)

    def _register(self) -> None:
        import socket

        data = self._http("POST", "/api/register", {"uid": self.user_id, "name": self.name,
                                                    "hostname": socket.gethostname()}, signed=False)
        self._save_secret(data["secret"])
        log.info("Админы компьютерт бүртгэгдлээ (%s)", self.base)

    # ------------------------------------------------------------------ HTTP
    def _http(self, method: str, path: str, body: dict | None = None, signed: bool = True, timeout: float = 20):
        raw = b"" if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if signed:
            ts = str(int(time.time()))
            headers.update({"X-Uid": self.user_id, "X-Ts": ts, "X-Sig": sign(self._secret, ts, method, path, raw)})
        req = urllib.request.Request(self.base + path, data=raw if method == "POST" else None, headers=headers,
                                     method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as exc:
            try:
                msg = json.loads(exc.read().decode("utf-8")).get("message", "")
            except (ValueError, UnicodeDecodeError, AttributeError):
                msg = ""
            raise _HttpError(exc.code, msg or f"HTTP {exc.code}") from None
        except (urllib.error.URLError, OSError) as exc:
            raise NetworkError(str(exc)) from None

    def _signed(self, method: str, path: str, body: dict | None = None, timeout: float = 20):
        with self._lock:
            if not self._secret:
                try:
                    self._register()
                except _HttpError as exc:
                    raise _friendly(exc) from None
        try:
            return self._http(method, path, body, timeout=timeout)
        except _HttpError as exc:
            if exc.status == 401 and str(exc) == "unknown device":
                # Админ түлхүүрийг шинэчилсэн (эсвэл шинэ сервер) → дахин бүртгүүлээд нэг удаа оролдоно
                with self._lock:
                    self._save_secret(None)
                    try:
                        self._register()
                    except _HttpError as exc2:
                        raise _friendly(exc2) from None
                try:
                    return self._http(method, path, body, timeout=timeout)
                except _HttpError as exc2:
                    raise _friendly(exc2) from None
            raise _friendly(exc) from None

    # ------------------------------------------------------------------ ShareClient-тэй ижил интерфэйс
    def available(self) -> bool:
        try:
            return self._http("GET", "/api/ping", signed=False, timeout=4).get("ok") is True
        except (NetworkError, _HttpError):
            return False

    def select(self, table: str, columns: str = "*", **filters) -> list[dict]:  # noqa: ARG002
        if table != "wt_company":
            raise ShareError(f"unknown table: {table}")
        d = self._signed("GET", "/api/settings")
        return [{"id": 1, "settings": d.get("settings") or {}}]

    def rpc(self, fn: str, params: dict | None = None, timeout: float = 20):
        if fn != "wt_push":
            raise ShareError(f"unknown function: {fn}")
        p = dict(params or {})
        p.update(p_name=self.name, p_version=__version__, p_windows_user=_windows_user())
        return self._signed("POST", "/api/push", p, timeout=timeout).get("result")

    def set_name(self, name: str) -> None:
        self.name = name

    # ------------------------------------------------------------------ шинэчлэл
    def update_meta(self) -> dict | None:
        try:
            return self._http("GET", "/api/update/version.json", signed=False, timeout=10)
        except (_HttpError, NetworkError):
            return None

    def update_url(self, name: str) -> str:
        return f"{self.base}/api/update/{name}"


def make_client(cfg):
    """Аль замаар өгөгдлөө илгээх вэ:
    админы компьютер — өөрийн C:\\AjliinButeemjData-д шууд (ShareClient); ажилтан — админы серверт (HubClient)."""
    from pathlib import Path

    from .config import load_admin, load_hub, load_share
    from .share import ShareClient, make_uid

    uid, name = make_uid(), cfg["employee_name"]
    info = load_share()
    local = (info or {}).get("local_path")
    if load_admin() and local and Path(local).is_dir():
        return ShareClient({"local_path": local}, uid, name)
    hub = load_hub()
    return HubClient(hub, uid, name) if hub else None


class _HttpError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def _friendly(exc: _HttpError) -> ShareError:
    msg = str(exc)
    if msg == "bad signature":
        return ShareError("device key mismatch")
    return ShareError(msg)


def _windows_user() -> str:
    try:
        return getpass.getuser()
    except Exception:  # noqa: BLE001
        return ""
