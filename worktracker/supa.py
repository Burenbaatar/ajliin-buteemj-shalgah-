"""Supabase-тэй холбогдох жижиг client (Auth + REST). Нэмэлт сан шаардахгүй.

- Нэвтэрсэн session (refresh token)-ийг дискэнд хадгална → компьютер асахад автоматаар нэвтэрнэ.
- Интернэтгүй үед хадгалсан профайлаар ажилласаар байна, холбогдохоор sync хийнэ.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .net import ssl_context

log = logging.getLogger(__name__)

ROLE_TEXT = {"pending": "Хүлээгдэж буй", "employee": "Ажилтан", "manager": "Менежер", "admin": "Админ"}

_MESSAGES = {
    "invalid login credentials": "Имэйл эсвэл нууц үг буруу байна",
    "user already registered": "Энэ имэйлээр бүртгэл аль хэдийн үүссэн байна",
    "email not confirmed": "Имэйл баталгаажаагүй. Админ Supabase дээр 'Confirm email'-ийг унтраах хэрэгтэй",
    "password should be at least": "Нууц үг хамгийн багадаа 6 тэмдэгт байна",
    "unable to validate email address": "Имэйл хаяг буруу байна",
    "account not approved": "Админ таны бүртгэлийг баталгаажуулаагүй байна",
    "admin only": "Зөвхөн админ хийх боломжтой",
    "last admin cannot be removed": "Сүүлийн админыг буулгах боломжгүй",
    "invalid refresh token": "Нэвтрэлт хүчингүй болсон. Дахин нэвтэрнэ үү",
    "refresh token not found": "Нэвтрэлт хүчингүй болсон. Дахин нэвтэрнэ үү",
    "signups not allowed": "Шинээр бүртгүүлэх хаалттай байна (админтай холбогдоно уу)",
    "rate limit": "Хэт олон оролдлого. Түр хүлээгээд дахин оролдоно уу",
    "duplicate key": "Ийм нэр аль хэдийн байна",
}


class SupaError(Exception):
    """Серверээс ирсэн алдаа (нэвтрэлт, эрх, өгөгдөл)."""

    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status

    @property
    def friendly(self) -> str:
        low = str(self).lower()
        for key, text in _MESSAGES.items():
            if key in low:
                return text
        return str(self)


class NetworkError(Exception):
    """Интернэт / сервер рүү холбогдож чадсангүй."""

    friendly = "Сервертэй холбогдож чадсангүй. Интернэт холболтоо шалгана уу"


class SupaClient:
    def __init__(self, url: str, key: str, session_path: Path):
        self.url = url.rstrip("/")
        self.key = key.strip()
        self.session_path = Path(session_path)
        self.session: dict | None = None  # access_token, refresh_token, expires_at, user, profile
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ HTTP
    def _request(self, method: str, path: str, body=None, query=None, auth: bool = True,
                 timeout: float = 20, retry: bool = True, prefer: str | None = None):
        url = self.url + path
        if query:
            url += "?" + urllib.parse.urlencode(query, doseq=True, safe=",.()*:")
        headers = {"apikey": self.key, "Content-Type": "application/json", "Accept": "application/json"}
        if prefer:
            headers["Prefer"] = prefer
        if auth and self.session:
            headers["Authorization"] = f"Bearer {self.session['access_token']}"
        elif not self.key.startswith("sb_"):  # хуучин anon JWT түлхүүр
            headers["Authorization"] = f"Bearer {self.key}"
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=ssl_context()) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                err = json.loads(raw.decode("utf-8"))
            except ValueError:
                err = {}
            msg = (err.get("msg") or err.get("message") or err.get("error_description")
                   or err.get("error") or raw.decode("utf-8", "replace")[:200] or f"HTTP {exc.code}")
            # Access token хугацаа дууссан → шинэчлээд нэг удаа дахин оролдоно
            if auth and retry and self.session and exc.code == 401 and "jwt" in str(msg).lower():
                self.refresh()
                return self._request(method, path, body, query, auth, timeout, retry=False, prefer=prefer)
            raise SupaError(str(msg), exc.code) from None
        except (urllib.error.URLError, OSError) as exc:
            raise NetworkError(str(exc)) from None
        if not raw:
            return None
        return json.loads(raw.decode("utf-8"))

    # ------------------------------------------------------------------ session
    def _set_session(self, data: dict) -> None:
        old_profile = (self.session or {}).get("profile")
        self.session = {
            "access_token": data["access_token"],
            "refresh_token": data["refresh_token"],
            "expires_at": time.time() + float(data.get("expires_in", 3600)),
            "user": {"id": data["user"]["id"], "email": data["user"].get("email", "")},
            "profile": old_profile,
        }
        self._save()

    def _save(self) -> None:
        if not self.session:
            return
        tmp = self.session_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.session, ensure_ascii=False), encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, self.session_path)

    def _clear(self) -> None:
        self.session = None
        try:
            self.session_path.unlink()
        except FileNotFoundError:
            pass

    @property
    def user_id(self) -> str | None:
        return self.session["user"]["id"] if self.session else None

    @property
    def email(self) -> str:
        return self.session["user"]["email"] if self.session else ""

    def restore(self) -> bool:
        """Хадгалсан session-ийг сэргээнэ. Интернэтгүй бол хадгалсан хэвээр нь үлдээнэ."""
        try:
            self.session = json.loads(self.session_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.session = None
            return False
        try:
            self.refresh()
        except NetworkError:
            log.info("Интернэтгүй — хадгалсан нэвтрэлтээр үргэлжлүүлнэ")
        except SupaError as exc:
            log.warning("Хадгалсан нэвтрэлт хүчингүй: %s", exc)
            self._clear()
            return False
        return True

    def refresh(self) -> None:
        with self._lock:
            if not self.session:
                raise SupaError("not signed in", 401)
            data = self._request("POST", "/auth/v1/token", {"refresh_token": self.session["refresh_token"]},
                                 query={"grant_type": "refresh_token"}, auth=False)
            self._set_session(data)

    def ensure_fresh(self) -> None:
        with self._lock:
            if self.session and self.session["expires_at"] - time.time() < 120:
                self.refresh()

    def sign_in(self, email: str, password: str) -> None:
        with self._lock:
            self.session = None
            data = self._request("POST", "/auth/v1/token", {"email": email.strip(), "password": password},
                                 query={"grant_type": "password"}, auth=False)
            self._set_session(data)

    def sign_up(self, email: str, password: str, full_name: str) -> bool:
        """Бүртгүүлнэ. True = шууд нэвтэрсэн, False = имэйл баталгаажуулалт шаардлагатай."""
        with self._lock:
            self.session = None
            data = self._request("POST", "/auth/v1/signup",
                                 {"email": email.strip(), "password": password,
                                  "data": {"full_name": full_name.strip()}}, auth=False)
            if data and data.get("access_token"):
                self._set_session(data)
                return True
            return False

    def sign_out(self) -> None:
        with self._lock:
            if self.session:
                try:
                    self._request("POST", "/auth/v1/logout", {}, timeout=5, retry=False)
                except (SupaError, NetworkError):
                    pass
            self._clear()

    def update_password(self, new_password: str) -> None:
        self.ensure_fresh()
        self._request("PUT", "/auth/v1/user", {"password": new_password})

    # ------------------------------------------------------------------ өгөгдөл
    def select(self, table: str, columns: str = "*", **filters) -> list[dict]:
        """select("wt_daily", "user_id,day", day="eq.2026-11-10", order="day")"""
        self.ensure_fresh()
        query = [("select", columns)]
        for k, v in filters.items():
            for item in (v if isinstance(v, (list, tuple)) else [v]):
                query.append((k, item))
        return self._request("GET", f"/rest/v1/{table}", query=query) or []

    def rpc(self, fn: str, params: dict | None = None, timeout: float = 20):
        self.ensure_fresh()
        return self._request("POST", f"/rest/v1/rpc/{fn}", params or {}, timeout=timeout)

    def fetch_profile(self) -> dict | None:
        """Өөрийн профайл (эрх, хэлтэс). Хадгалж авна — интернэтгүй үед ашиглана."""
        rows = self.select("wt_profiles", "user_id,email,full_name,role,department_id,active",
                           user_id=f"eq.{self.user_id}")
        profile = rows[0] if rows else None
        if self.session is not None:
            self.session["profile"] = profile
            self._save()
        return profile

    @property
    def cached_profile(self) -> dict | None:
        return (self.session or {}).get("profile")
