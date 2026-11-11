"""Ажилтны өгөгдлийг JSON болгоод админы хуваалцсан хавтас руу хуулна (share.py).

- Минут тутам, мөн төлөв өөрчлөгдөх бүрт: одоогийн төлөв + өнөөдрийн нийт дүн
- Эхлэхэд: сүүлийн 7 хоногийн дүн (хавтас холбогдоогүй байсан үеийг нөхөх)
- 10 минут тутам: админы өөрчилсөн компанийн тохиргоог татна
- Програм хаагдахад (quit): эцсийн дүн + "offline"
Бүх илгээлт idempotent (нийт дүнгээр солино) тул давтан илгээх нь аюулгүй.
"""
from __future__ import annotations

import logging
import platform
import socket
import threading
import time
import uuid
from datetime import date, timedelta

from .config import COMPANY_KEYS
from .report import merge_periods
from .storage import Storage
from .share import NetworkError, ShareClient, ShareError

log = logging.getLogger(__name__)

HISTORY_DAYS = 7
MAX_APPS = 60
SETTINGS_EVERY_SEC = 600


class CloudSync:
    def __init__(self, cfg: dict, storage: Storage, tracker, api: ShareClient):
        self.cfg = cfg
        self.storage = storage
        self.tracker = tracker
        self.api = api
        self.interval = max(15.0, float(cfg.get("cloud", {}).get("push_interval_sec", 60)))
        self.device_id = storage.get_meta("device_id")
        if not self.device_id:
            self.device_id = str(uuid.uuid4())
            storage.set_meta("device_id", self.device_id)
        self.hostname = socket.gethostname()
        self.platform = f"{platform.system()} {platform.release()}"

        self.last_ok: float | None = None
        self.last_error: str | None = None
        self._last_settings = 0.0
        self._history_done = False
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

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
            self.push_day(date.today().isoformat(), live=True, status="offline", timeout=5)
        except Exception as exc:  # noqa: BLE001
            log.info("offline төлөв илгээж чадсангүй: %s", exc)

    def status_text(self) -> str:
        if self.last_error:
            return f"Алдаа: {self.last_error[:42]}"
        if self.last_ok is None:
            return "Холбогдож байна…"
        ago = int(time.monotonic() - self.last_ok)
        return f"Холбогдсон ({ago} сек өмнө)" if ago < 120 else f"Холбогдсон ({ago // 60} мин өмнө)"

    # ------------------------------------------------------------------ дотоод
    def _run(self) -> None:
        self._stop.wait(2.5)  # tracker анхны шалгалтаа хийж амжтал (апп, камерын төлөв)
        while not self._stop.is_set():
            try:
                if time.monotonic() - self._last_settings >= SETTINGS_EVERY_SEC or not self._last_settings:
                    self.pull_settings()
                if not self._history_done:
                    self._push_history()
                    self._history_done = True
                self.push_day(date.today().isoformat(), live=True)
                self._ok()
            except Exception as exc:  # noqa: BLE001
                self._fail(exc)
            self._wake.wait(self.interval)
            self._wake.clear()
            if not self._stop.is_set():
                self._stop.wait(1)  # төлөв тогтворжих хүртэл

    def _ok(self) -> None:
        if self.last_error:
            log.info("Админы хавтастай дахин холбогдлоо")
        self.last_ok = time.monotonic()
        self.last_error = None

    def _fail(self, exc: Exception) -> None:
        msg = exc.friendly if isinstance(exc, (ShareError, NetworkError)) else str(exc)
        self._history_done = False  # хавтас эргэж холбогдоход өмнөх өдрүүдийг ч нөхөж бичнэ
        if msg != self.last_error:  # ижил алдааг дахин дахин логлохгүй
            log.warning("Админы хавтас руу хуулж чадсангүй: %s (%s)", msg, exc)
        self.last_error = msg

    def pull_settings(self) -> None:
        """Админы тохиргоог (wt_company) cfg-д хэрэглэнэ. Tracker секунд тутам cfg уншдаг."""
        rows = self.api.select("wt_company", "settings", id="eq.1")
        self._last_settings = time.monotonic()
        settings = (rows[0].get("settings") if rows else None) or {}
        for k in COMPANY_KEYS:
            if k in settings and settings[k] is not None:
                self.cfg[k] = settings[k]

    def _push_history(self) -> None:
        today = date.today()
        oldest = (today - timedelta(days=HISTORY_DAYS)).isoformat()
        for day in self.storage.days_with_data():
            if oldest <= day < today.isoformat():
                self.push_day(day, live=False)

    def push_day(self, day: str, live: bool, status: str | None = None, timeout: float = 20) -> None:
        snap = None
        if live and self.tracker is not None:
            self.tracker.flush_now()
            snap = self.tracker.snapshot()
        d = self.storage.get_day(day)
        params = {
            "p_device_id": self.device_id,
            "p_hostname": self.hostname,
            "p_platform": self.platform,
            "p_day": day,
            "p_work_seconds": int(d["work_seconds"]),
            "p_away_seconds": int(d["away_seconds"]),
            "p_first_seen": d["first_seen"],
            "p_last_seen": d["last_seen"],
            "p_apps": [[a, int(s)] for a, s in d["apps"][:MAX_APPS]],
            "p_periods": [[p["status"], p["start"], p["end"]] for p in merge_periods(d["periods"])][-300:],
            "p_flags": [[f["kind"], f["start"], f["end"]] for f in d.get("flags", [])][-200:],
            "p_live": live,
        }
        if live:
            params["p_status"] = status or (snap["status"] if snap else "working")
            params["p_current_app"] = snap["app"] if snap else None
            params["p_camera_ok"] = snap["camera_ok"] if snap else None
            params["p_camera_state"] = snap.get("camera_state") if snap else None
        self.api.rpc("wt_push", params, timeout=timeout)
