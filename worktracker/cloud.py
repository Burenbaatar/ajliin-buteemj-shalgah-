"""Supabase руу (сервергүй вэб самбар) мэдээлэл илгээнэ.

- Минут тутам, мөн төлөв өөрчлөгдөх бүрт: одоогийн төлөв + өнөөдрийн нийт дүн
- Програм эхлэхэд: сүүлийн 7 хоногийн дүнг (интернэтгүй байсан үеийг нөхөх)
- Хаагдахад: "offline"
Бүх илгээлт idempotent (нийт дүнгээр солино), тиймээс давтан илгээх нь аюулгүй.
"""
from __future__ import annotations

import logging
import platform
import secrets
import socket
import threading
import time
import uuid
from datetime import date, timedelta

from .net import post_json
from .report import merge_periods
from .storage import Storage

log = logging.getLogger(__name__)

HISTORY_DAYS = 7
MAX_APPS = 60


class CloudSync:
    def __init__(self, cfg: dict, storage: Storage, tracker):
        c = cfg["cloud"]
        self.cfg = cfg
        self.url = str(c["supabase_url"]).rstrip("/")
        self.key = str(c["supabase_key"]).strip()
        self.company_key = str(c["company_key"]).strip()
        self.interval = max(15.0, float(c.get("push_interval_sec", 60)))
        self.storage = storage
        self.tracker = tracker

        self.device_id = storage.get_meta("device_id")
        if not self.device_id:
            self.device_id = str(uuid.uuid4())
            storage.set_meta("device_id", self.device_id)
        self.device_secret = storage.get_meta("device_secret")
        if not self.device_secret:
            self.device_secret = secrets.token_hex(24)
            storage.set_meta("device_secret", self.device_secret)

        self.last_ok: float | None = None  # time.monotonic()
        self.last_error: str | None = None
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._history_done = False

    # ------------------------------------------------------------------ public
    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="cloud", daemon=True)
        self._thread.start()

    def poke(self) -> None:
        """Төлөв өөрчлөгдсөн — даруй илгээнэ."""
        self._wake.set()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=5)
        try:
            self._push_day(date.today().isoformat(), live=True, status="offline", timeout=5)
        except Exception as exc:  # noqa: BLE001
            log.info("offline төлөв илгээж чадсангүй: %s", exc)

    def status_text(self) -> str:
        if self.last_error:
            return f"Алдаа: {self.last_error[:40]}"
        if self.last_ok is None:
            return "Холбогдож байна…"
        ago = int(time.monotonic() - self.last_ok)
        return f"Холбогдсон ({ago} сек өмнө)" if ago < 120 else f"Холбогдсон ({ago // 60} мин өмнө)"

    # ------------------------------------------------------------------ дотоод
    def _run(self) -> None:
        self._stop.wait(2.5)  # tracker анхны шалгалтаа хийж амжтал (апп, камерын төлөв)
        while not self._stop.is_set():
            try:
                if not self._history_done:
                    self._push_history()
                    self._history_done = True
                self._push_day(date.today().isoformat(), live=True)
                self._ok()
            except Exception as exc:  # noqa: BLE001
                self._fail(exc)
            self._wake.wait(self.interval)
            self._wake.clear()
            if not self._stop.is_set():
                self._stop.wait(1)  # төлөв тогтворжих хүртэл

    def _ok(self) -> None:
        if self.last_error:
            log.info("Самбартай дахин холбогдлоо")
        self.last_ok = time.monotonic()
        self.last_error = None

    def _fail(self, exc: Exception) -> None:
        msg = str(exc)
        if msg != self.last_error:  # ижил алдааг дахин дахин логлохгүй
            log.warning("Самбар руу илгээж чадсангүй: %s", msg)
        self.last_error = msg

    def _push_history(self) -> None:
        today = date.today()
        oldest = (today - timedelta(days=HISTORY_DAYS)).isoformat()
        for day in self.storage.days_with_data():
            if oldest <= day < today.isoformat():
                self._push_day(day, live=False)

    def _push_day(self, day: str, live: bool, status: str | None = None, timeout: float = 20) -> None:
        snap = None
        if live and self.tracker is not None:
            self.tracker.flush_now()
            snap = self.tracker.snapshot()
        d = self.storage.get_day(day)
        periods = [[p["status"], p["start"], p["end"]] for p in merge_periods(d["periods"])]
        params = {
            "p_company_key": self.company_key,
            "p_device_id": self.device_id,
            "p_device_secret": self.device_secret,
            "p_employee": self.cfg["employee_name"],
            "p_hostname": socket.gethostname(),
            "p_platform": f"{platform.system()} {platform.release()}",
            "p_day": day,
            "p_work_seconds": int(d["work_seconds"]),
            "p_away_seconds": int(d["away_seconds"]),
            "p_first_seen": d["first_seen"],
            "p_last_seen": d["last_seen"],
            "p_apps": [[a, int(s)] for a, s in d["apps"][:MAX_APPS]],
            "p_periods": periods[-300:],
            "p_live": live,
        }
        if live:
            params["p_status"] = status or (snap["status"] if snap else "working")
            params["p_current_app"] = snap["app"] if snap else None
            params["p_camera_ok"] = snap["camera_ok"] if snap else None
        self._rpc("wt_push", params, timeout)

    def _rpc(self, fn: str, params: dict, timeout: float) -> bytes:
        headers = {"apikey": self.key}
        # Шинэ "sb_publishable_..." түлхүүр JWT биш тул Authorization толгойд хийхгүй
        if not self.key.startswith("sb_"):
            headers["Authorization"] = f"Bearer {self.key}"
        return post_json(f"{self.url}/rest/v1/rpc/{fn}", params, headers, timeout=timeout)


def cloud_configured(cfg: dict) -> bool:
    c = cfg.get("cloud", {})
    return bool(c.get("enabled") and c.get("supabase_url") and c.get("supabase_key") and c.get("company_key"))
