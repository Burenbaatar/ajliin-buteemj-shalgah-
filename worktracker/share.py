"""Админы хуваалцсан хавтас (\\\\ADMIN-PC\\AjliinButeemj) — сервер, SQL шаардахгүй, зөвхөн JSON файлууд.

Хавтасны бүтэц:
    company.json                      тохиргоо, хэлтэс, хүмүүсийн нэр/хэлтэс/идэвхтэй эсэх (зөвхөн админ бичнэ)
    people/<uid>/profile.json         ажилтны нэр, Windows хэрэглэгч, компьютер (ажилтны програм бичнэ)
    people/<uid>/live.json            одоогийн төлөв (минут тутам)
    people/<uid>/days/<өдөр>.json     өдрийн дүн

Компьютер бүр зөвхөн өөрийн people/<uid>/ хавтсанд бичдэг тул хоёр компьютер нэг файлыг зэрэг засахгүй.
ShareClient нь самбар (dashdata) болон CloudSync-ийн хэрэглэдэг select()/rpc() интерфэйстэй.
"""
from __future__ import annotations

import getpass
import json
import logging
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger(__name__)

DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
STATUSES = ("working", "away", "offline")
CAMERA_STATES = ("ok", "blocked", "static", "none")

_MESSAGES = {
    "admin only": "Зөвхөн админ хийх боломжтой",
    "duplicate key": "Ийм нэр аль хэдийн байна",
    "name required": "Нэр оруулна уу",
    "department not found": "Хэлтэс олдсонгүй",
    "user not found": "Хүн олдсонгүй",
    "removed by admin": "Админ таныг жагсаалтаас хассан — өгөгдөл илгээхгүй",
}


class ShareError(Exception):
    """Буруу өгөгдөл эсвэл эрхгүй үйлдэл."""

    @property
    def friendly(self) -> str:
        low = str(self).lower()
        return next((text for key, text in _MESSAGES.items() if key in low), str(self))


class NetworkError(Exception):
    """Хуваалцсан хавтас руу хандаж чадсангүй."""

    friendly = "Админы хавтас олдсонгүй (админы компьютер асаалттай, нэг сүлжээнд байгаа эсэхийг шалгана уу)"


# ============================================================================ туслах
def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def make_uid() -> str:
    """Ажилтныг автоматаар таних: <windows-хэрэглэгч>@<компьютер>."""
    try:
        user = getpass.getuser()
    except Exception:  # noqa: BLE001
        user = "user"
    raw = f"{user}@{socket.gethostname()}"
    return re.sub(r"[^A-Za-z0-9._@-]+", "_", raw)[:80] or "user"


def default_name() -> str:
    try:
        return getpass.getuser()
    except Exception:  # noqa: BLE001
        return ""


def read_json(path: Path, default=None):
    try:
        with path.open(encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except ValueError:
        log.warning("Эвдэрсэн JSON: %s", path)
        return default


def write_json(path: Path, data) -> None:
    """Түр файлд бичээд солино. Өөр компьютер яг тэр үед уншиж байвал (Windows түгжээ) хэд дахин оролдоно."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    for attempt in range(8):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 7:
                try:
                    tmp.unlink()
                except OSError:
                    pass
                raise
            time.sleep(0.25)


def _clamp(v, lo: int, hi: int) -> int:
    try:
        return max(lo, min(int(v or 0), hi))
    except (TypeError, ValueError):
        return lo


def _short(v, n: int):
    return None if v is None else str(v)[:n]


def _match(row: dict, key: str, cond: str) -> bool:
    op, _, val = cond.partition(".")
    cur = row.get(key)
    cur = "" if cur is None else (str(cur).lower() if isinstance(cur, bool) else str(cur))
    ops = {"eq": cur == val, "neq": cur != val, "gte": cur >= val, "lte": cur <= val, "gt": cur > val,
           "lt": cur < val}
    if op not in ops:
        raise ShareError(f"unsupported filter: {op}")
    return ops[op]


def _day_bounds(conds: list[str]) -> tuple[str | None, str | None]:
    lo = hi = None
    for c in conds:
        op, _, val = c.partition(".")
        if op == "eq":
            lo = hi = val
        elif op in ("gte", "gt"):
            lo = val
        elif op in ("lte", "lt"):
            hi = val
    return lo, hi


# ============================================================================ холболт
def resolve_root(info: dict) -> Path:
    """share.json → унших/бичих зам. Админы компьютер дээр локал замыг шууд ашиглана."""
    local = info.get("local_path")
    if local and Path(local).is_dir():
        return Path(local)
    share = str(info.get("share") or "")
    if sys.platform == "darwin" and share.startswith("\\\\"):
        from .config import data_dir

        return data_dir() / "share"  # mount_smbfs-ээр энд холбогдоно
    return Path(share)


_connect_lock = threading.Lock()
_last_connect = 0.0


def ensure_connected(info: dict, root: Path) -> None:
    """Хавтас харагдахгүй бол share.json дахь тусгай хэрэглэгчээр автоматаар холбогдоно (60 сек тутамд нэг л удаа)."""
    global _last_connect
    share, user, pw = str(info.get("share") or ""), info.get("user"), info.get("password")
    if not share.startswith("\\\\") or not user:
        return
    with _connect_lock:
        if time.monotonic() - _last_connect < 60:
            return
        _last_connect = time.monotonic()
        try:
            if sys.platform == "win32":
                flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                r = subprocess.run(["net", "use", share, str(pw or ""), f"/user:{user}", "/persistent:no"],
                                   capture_output=True, timeout=30, creationflags=flags)
                if r.returncode:
                    log.warning("Хавтсанд холбогдож чадсангүй: %s",
                                (r.stderr or r.stdout).decode("utf-8", "replace").strip()[:200])
            elif sys.platform == "darwin":
                from urllib.parse import quote

                host, _, name = share.lstrip("\\").partition("\\")
                account = str(user).split("\\")[-1]
                root.mkdir(parents=True, exist_ok=True)
                if not os.path.ismount(root):
                    url = f"//{quote(account)}:{quote(str(pw or ''))}@{host}/{quote(name)}"
                    subprocess.run(["mount_smbfs", "-N", url, str(root)], capture_output=True, timeout=30)
        except (OSError, subprocess.SubprocessError) as exc:
            log.warning("Хавтсанд холбогдож чадсангүй: %s", exc)


# ============================================================================ client
class ShareClient:
    def __init__(self, info: dict, uid: str, name: str = ""):
        self.info = info
        self.root = resolve_root(info)
        self.user_id = uid
        self.name = name
        self.is_admin = False  # админ нууц үгээр самбар нээхэд True болно
        self._profile_done = False
        self._removed = False
        self._removed_checked = -1e9

    # ------------------------------------------------------------------ дотоод
    @property
    def people_dir(self) -> Path:
        return self.root / "people"

    def available(self) -> bool:
        try:
            if (self.root / "company.json").is_file() or self.people_dir.is_dir():
                return True
        except OSError:
            pass
        ensure_connected(self.info, self.root)
        try:
            return (self.root / "company.json").is_file() or self.people_dir.is_dir()
        except OSError:
            return False

    def _need(self) -> None:
        if not self.available():
            raise NetworkError(str(self.root))

    def company(self) -> dict:
        c = read_json(self.root / "company.json", {}) or {}
        c.setdefault("settings", {})
        c.setdefault("departments", [])
        c.setdefault("people", {})
        c.setdefault("removed", {})  # админ бүрмөсөн устгасан хүмүүс: uid → {full_name, removed_at}
        c.setdefault("next_dept_id", 1)
        return c

    def _is_removed(self) -> bool:
        """Энэ ажилтныг админ устгасан эсэх (30 сек кэштэй — хавтсыг дэмий уншихгүй)."""
        now = time.monotonic()
        if now - self._removed_checked > 30:
            self._removed_checked = now
            self._removed = self.user_id in self.company()["removed"]
            if self._removed:
                self._profile_done = False  # сэргээвэл profile.json-г (нэр, огноо) дахин бичнэ
        return self._removed

    def _save_company(self, c: dict) -> None:
        write_json(self.root / "company.json", c)

    def _require_admin(self) -> None:
        if not self.is_admin:
            raise ShareError("admin only")

    def _person_dirs(self) -> list[Path]:
        try:
            dirs = [p for p in self.people_dir.iterdir() if p.is_dir()]
        except FileNotFoundError:
            return []
        removed = self.company()["removed"]
        out = []
        for d in dirs:
            if d.name in removed:  # устгасны дараа програм нь хэдэн секунд бичсэн үлдэгдэл
                if self.is_admin:
                    shutil.rmtree(d, ignore_errors=True)
                continue
            out.append(d)
        return out

    # ------------------------------------------------------------------ унших
    def select(self, table: str, columns: str = "*", **filters) -> list[dict]:
        try:
            self._need()
            rows = self._rows(table, filters)
        except OSError as exc:
            raise NetworkError(str(exc)) from None
        flat = [(k, v) for k, vs in filters.items() if k != "order"
                for v in (vs if isinstance(vs, (list, tuple)) else [vs])]
        rows = [r for r in rows if all(_match(r, k, v) for k, v in flat)]
        order = filters.get("order")
        if order:
            key, _, direction = str(order).partition(".")
            rows.sort(key=lambda r: "" if r.get(key) is None else str(r.get(key)).lower(),
                      reverse=direction == "desc")
        if columns.strip() != "*":
            wanted = [c.strip() for c in columns.split(",") if c.strip()]
            rows = [{c: r.get(c) for c in wanted} for r in rows]
        return rows

    def _rows(self, table: str, filters: dict) -> list[dict]:
        if table == "wt_company":
            return [{"id": 1, "settings": self.company()["settings"]}]
        if table == "wt_departments":
            return [dict(d) for d in self.company()["departments"]]
        if table == "wt_profiles":
            people = self.company()["people"]
            out = []
            for d in self._person_dirs():
                prof = read_json(d / "profile.json", {}) or {}
                over = people.get(d.name, {})
                out.append({
                    "user_id": d.name,
                    "email": d.name,
                    "full_name": over.get("full_name") or prof.get("full_name") or "",
                    "hostname": prof.get("hostname", ""),
                    "role": "employee",
                    "department_id": over.get("department_id"),
                    "active": over.get("active", True),
                    "created_at": prof.get("created_at"),
                })
            return out
        if table == "wt_live":
            out = []
            for d in self._person_dirs():
                lv = read_json(d / "live.json")
                if lv:
                    out.append(dict(lv, user_id=d.name))
            return out
        if table == "wt_daily":
            day_filter = filters.get("day")
            conds = list(day_filter) if isinstance(day_filter, (list, tuple)) else ([day_filter] if day_filter else [])
            lo, hi = _day_bounds(conds)
            out = []
            for d in self._person_dirs():
                for f in self._day_files(d / "days", lo, hi):
                    row = read_json(f)
                    if row:
                        out.append(dict(row, user_id=d.name, device_id=d.name, day=f.stem))
            return out
        raise ShareError(f"unknown table: {table}")

    @staticmethod
    def _day_files(days_dir: Path, lo: str | None, hi: str | None) -> list[Path]:
        if lo and hi and DAY_RE.match(lo) and DAY_RE.match(hi):
            d0, d1 = date.fromisoformat(lo), date.fromisoformat(hi)
            files = []
            while d0 <= d1 and len(files) < 400:
                f = days_dir / f"{d0.isoformat()}.json"
                if f.is_file():
                    files.append(f)
                d0 += timedelta(days=1)
            return files
        try:
            return [f for f in days_dir.glob("*.json") if DAY_RE.match(f.stem)
                    and (lo is None or f.stem >= lo) and (hi is None or f.stem <= hi)]
        except OSError:
            return []

    # ------------------------------------------------------------------ бичих
    def rpc(self, fn: str, params: dict | None = None, timeout: float = 20):  # noqa: ARG002 — api-тай ижил
        method = getattr(self, "_" + fn, None) if fn.startswith("wt_") else None
        if method is None:
            raise ShareError(f"unknown function: {fn}")
        try:
            if fn != "wt_admin_telegram_configured" and fn != "wt_admin_set_telegram":
                self._need()
            return method(params or {})
        except OSError as exc:
            raise NetworkError(str(exc)) from None

    def _wt_push(self, p: dict) -> str:
        """Ажилтны програм: өөрийн өдрийн дүн, одоогийн төлөвийг өөрийн хавтсанд бичнэ."""
        if self._is_removed():
            raise ShareError("removed by admin")
        day = str(p.get("p_day") or "")
        if not DAY_RE.match(day):
            raise ShareError("invalid day")
        live = bool(p.get("p_live", True))
        status = p.get("p_status")
        if live and status not in STATUSES:
            raise ShareError("invalid status")
        cam = p.get("p_camera_state")
        if cam is not None and cam not in CAMERA_STATES:
            cam = None
        me = self.people_dir / self.user_id
        hostname = _short(p.get("p_hostname") or "", 100)
        now = utcnow_iso()

        if not self._profile_done:
            old = read_json(me / "profile.json", {}) or {}
            write_json(me / "profile.json", {
                "full_name": self.name[:100], "windows_user": default_name(), "hostname": hostname,
                "platform": _short(p.get("p_platform") or "", 50), "created_at": old.get("created_at") or now,
            })
            self._profile_done = True

        if not p.get("p_live_only"):  # апп солигдоход зөвхөн live.json-г бичнэ (өдрийн дүн минут тутам)
            write_json(me / "days" / f"{day}.json", {
                "hostname": hostname,
                "work_seconds": _clamp(p.get("p_work_seconds"), 0, 86400),
                "away_seconds": _clamp(p.get("p_away_seconds"), 0, 86400),
                "first_seen": _short(p.get("p_first_seen"), 8),
                "last_seen": _short(p.get("p_last_seen"), 8),
                "apps": p.get("p_apps") or [], "periods": p.get("p_periods") or [], "flags": p.get("p_flags") or [],
                "updated_at": now,
            })
        if live:
            old = read_json(me / "live.json", {}) or {}
            write_json(me / "live.json", {
                "hostname": hostname, "platform": _short(p.get("p_platform") or "", 50),
                "status": status, "current_app": _short(p.get("p_current_app"), 200),
                "camera_ok": p.get("p_camera_ok"), "camera_state": cam, "paused": bool(p.get("p_paused")),
                "camera_state_since": now if old.get("camera_state") != cam or not old
                else old.get("camera_state_since"),
                "status_since": now if old.get("status") != status else old.get("status_since", now),
                "last_seen_at": now,
            })
        return "ok"

    def set_name(self, name: str) -> None:
        """Ажилтан нэрээ солибол дараагийн бичилтэд profile.json шинэчлэгдэнэ."""
        self.name = name
        self._profile_done = False

    # ------------------------------------------------------------------ админ
    def _wt_admin_update_user(self, p: dict) -> None:
        self._require_admin()
        uid = str(p.get("p_user") or "")
        if not uid or not (self.people_dir / uid).is_dir():
            raise ShareError("user not found")
        c = self.company()
        dept = p.get("p_department_id")
        if dept is not None and not any(d["id"] == dept for d in c["departments"]):
            raise ShareError("department not found")
        entry = c["people"].setdefault(uid, {})
        name = str(p.get("p_full_name") or "").strip()[:100]
        if name:
            entry["full_name"] = name
        entry["department_id"] = dept
        entry["active"] = bool(p.get("p_active", True))
        self._save_company(c)

    def _wt_admin_save_department(self, p: dict) -> int:
        self._require_admin()
        name = str(p.get("p_name") or "").strip()[:100]
        if not name:
            raise ShareError("name required")
        c = self.company()
        dept_id = p.get("p_id")
        if any(d["name"].lower() == name.lower() and d["id"] != dept_id for d in c["departments"]):
            raise ShareError("duplicate key")
        if dept_id is None:
            dept_id = c["next_dept_id"]
            c["next_dept_id"] = dept_id + 1
            c["departments"].append({"id": dept_id, "name": name, "telegram_chat_id": None})
        else:
            dept = next((d for d in c["departments"] if d["id"] == dept_id), None)
            if not dept:
                raise ShareError("department not found")
            dept["name"] = name
        c["departments"].sort(key=lambda d: d["name"].lower())
        self._save_company(c)
        return dept_id

    def _wt_admin_delete_department(self, p: dict) -> None:
        self._require_admin()
        c = self.company()
        dept_id = p.get("p_id")
        c["departments"] = [d for d in c["departments"] if d["id"] != dept_id]
        for entry in c["people"].values():
            if entry.get("department_id") == dept_id:
                entry["department_id"] = None
        self._save_company(c)

    def _wt_admin_set_department_chat(self, p: dict) -> None:
        self._require_admin()
        c = self.company()
        for d in c["departments"]:
            if d["id"] == p.get("p_id"):
                d["telegram_chat_id"] = str(p.get("p_chat") or "").strip() or None
        self._save_company(c)

    def _wt_admin_save_settings(self, p: dict) -> None:
        self._require_admin()
        settings = p.get("p_settings")
        if not isinstance(settings, dict):
            raise ShareError("invalid settings")
        c = self.company()
        c["settings"] = settings
        self._save_company(c)

    def _wt_admin_remove_user(self, p: dict) -> None:
        """Хүнийг бүрмөсөн устгана: хавтас, бүх түүх, тохиргоо. Тэр компьютерийн програм дахин бичихгүй
        ("removed" жагсаалт) — андуурвал _wt_admin_restore_user-ээр сэргээнэ."""
        self._require_admin()
        uid = str(p.get("p_user") or "")
        target = self.people_dir / uid
        if not uid or "/" in uid or "\\" in uid or uid in (".", "..") or not target.is_dir():
            raise ShareError("user not found")
        prof = read_json(target / "profile.json", {}) or {}
        c = self.company()
        name = (c["people"].get(uid) or {}).get("full_name") or prof.get("full_name") or uid
        c["removed"][uid] = {"full_name": name, "removed_at": utcnow_iso()}
        c["people"].pop(uid, None)
        self._save_company(c)  # эхлээд хориглоно → устгах үед програм нь дахин бичихгүй
        shutil.rmtree(target, ignore_errors=True)

    def _wt_admin_restore_user(self, p: dict) -> None:
        self._require_admin()
        c = self.company()
        if c["removed"].pop(str(p.get("p_user") or ""), None) is None:
            raise ShareError("user not found")
        self._save_company(c)

    # Telegram токен хавтсанд биш, админы компьютер дээр (ажилтан харахгүй)
    def _wt_admin_set_telegram(self, p: dict) -> None:
        self._require_admin()
        from .config import load_admin, save_admin

        admin = load_admin()
        if admin is None:
            raise ShareError("admin only")
        admin["telegram_bot_token"] = str(p.get("p_token") or "").strip()
        save_admin(admin)

    def _wt_admin_telegram_configured(self, p: dict) -> bool:  # noqa: ARG002
        self._require_admin()
        from .config import load_admin

        return bool((load_admin() or {}).get("telegram_bot_token"))
