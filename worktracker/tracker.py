"""Гол логик: секунд тутам хүн байгаа эсэх, идэвхтэй аппыг шалгаж цаг тоолно."""
from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta

from . import activity
from .presence import PresenceMonitor
from .storage import Storage

log = logging.getLogger(__name__)

WORKING = "working"
AWAY = "away"
STATUS_TEXT = {WORKING: "Ажиллаж байна", AWAY: "Ажлаас гарсан"}

TICK_SEC = 1.0
FLUSH_SEC = 30.0
MAX_GAP_SEC = 10.0  # үүнээс урт завсар = компьютер унтсан; тоолохгүй


def _hms(dt: datetime) -> str:
    return dt.strftime("%H:%M:%S")


class Tracker:
    def __init__(self, cfg: dict, storage: Storage, presence: PresenceMonitor | None, on_report_due=None):
        self.cfg = cfg
        self.storage = storage
        self.presence = presence
        self.on_report_due = on_report_due  # callback(now)
        self.on_status_change = None  # callback() — төлөв солигдоход (самбар руу даруй илгээх)

        self.status = WORKING
        self.current_app: str | None = None
        self.camera_ok = False
        self.face_visible = False

        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

        now = datetime.now()
        self._day = now.date().isoformat()
        self._last_tick = time.monotonic()
        self._last_flush = self._last_tick
        self._last_report_check = 0.0
        self._last_evidence = self._last_tick  # эхлэхэд хүн байгаа гэж үзнэ (grace хугацаанд)

        # Хадгалаагүй байгаа өөрчлөлтүүд
        self._d_apps: dict[str, float] = defaultdict(float)
        self._d_work = 0.0
        self._d_away = 0.0
        self._d_first: str | None = None
        self._d_last: str | None = None
        # Царай түр алга болсон үеийн хугацаа: эргэж ирвэл ажилд, үгүй бол гарсанд тооцно
        self._pending: list[tuple[str, float]] = []

        # Өнөөдрийн нийт дүн (цонхонд харуулах)
        self._load_today()
        self._period_id: int | None = None

    # ------------------------------------------------------------------ public
    def start(self) -> None:
        self._open_period(WORKING, datetime.now())
        self._thread = threading.Thread(target=self._run, name="tracker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        with self._lock:
            self._commit_pending(to_work=False)  # баталгаажаагүй хугацааг ажилд тооцохгүй
            self._flush(datetime.now())

    def snapshot(self) -> dict:
        """Цонхонд харуулах одоогийн төлөв."""
        with self._lock:
            pending = sum(s for _, s in self._pending)
            apps = dict(self._today_apps)
            for app, sec in self._pending:
                apps[app] = apps.get(app, 0.0) + sec
            top = sorted(apps.items(), key=lambda kv: kv[1], reverse=True)
            return {
                "status": self.status,
                "status_text": STATUS_TEXT[self.status],
                "app": self.current_app,
                "work_seconds": self._today_work + pending,
                "away_seconds": self._today_away,
                "camera_ok": self.camera_ok,
                "face_visible": self.face_visible,
                "top_apps": top,
            }

    def flush_now(self) -> None:
        with self._lock:
            self._flush(datetime.now())

    # ------------------------------------------------------------------ дотоод
    def _load_today(self) -> None:
        d = self.storage.get_day(self._day)
        self._today_work = d["work_seconds"]
        self._today_away = d["away_seconds"]
        self._today_apps: dict[str, float] = dict(d["apps"])

    def _run(self) -> None:
        while not self._stop.wait(TICK_SEC):
            try:
                self._tick()
            except Exception:  # noqa: BLE001
                log.exception("tick алдаа")

    def _tick(self) -> None:
        cfg = self.cfg
        mono = time.monotonic()
        dt = mono - self._last_tick
        self._last_tick = mono
        now = datetime.now()

        with self._lock:
            # Компьютер унтаад сэрсэн: завсрыг тоолохгүй, шинэ үе эхлүүлнэ
            if dt > MAX_GAP_SEC:
                log.info("%.0f сек завсар (унтсан?) — тоолохгүй", dt)
                self._commit_pending(to_work=False)
                self._flush(now - timedelta(seconds=dt))
                if now.date().isoformat() != self._day:
                    self._day = now.date().isoformat()
                    self._load_today()
                self._last_evidence = mono
                self.status = WORKING
                self._open_period(WORKING, now)
                return

            # Шөнө дунд өдөр солигдох
            day = now.date().isoformat()
            if day != self._day:
                self._commit_pending(to_work=False)
                self._flush(now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(seconds=1))
                self._day = day
                self._load_today()
                self._open_period(self.status, now.replace(hour=0, minute=0, second=0, microsecond=0))

            # --- Мэдрэгчүүд
            locked = activity.is_screen_locked()
            app = None if locked else activity.get_active_app()
            idle = activity.get_idle_seconds()
            self.current_app = app

            cam_ok = bool(self.presence and self.presence.camera_ok)
            self.camera_ok = cam_ok
            fresh = max(3.0, 2.5 * float(cfg["camera_check_interval_sec"]))
            face = cam_ok and (mono - self.presence.last_face_time) <= fresh
            self.face_visible = face
            input_recent = idle is not None and idle <= float(cfg["input_presence_window_sec"])

            if locked:
                evidence = False
            elif cam_ok:
                evidence = face or (bool(cfg["input_counts_as_presence"]) and input_recent)
            elif idle is not None:
                evidence = idle <= float(cfg["no_camera_idle_timeout_sec"])
            else:
                evidence = True  # шалгах арга алга: ажиллаж байна гэж үзнэ

            app_key = app or activity.UNKNOWN_APP
            grace = float(cfg["away_grace_sec"])

            if evidence:
                self._last_evidence = mono
                self._commit_pending(to_work=True)
                self._add_work(app_key, dt, now)
                if self.status != WORKING:
                    self._switch(WORKING, now)
            elif self.status == WORKING and not locked and (mono - self._last_evidence) <= grace:
                # Түр харагдахгүй байна — шийдвэрийг хойшлуулна
                self._pending.append((app_key, dt))
            else:
                if self.status == WORKING:
                    pending = self._commit_pending(to_work=False)
                    self._switch(AWAY, now - timedelta(seconds=pending))
                self._add_away(dt)

            if mono - self._last_flush >= FLUSH_SEC:
                self._flush(now)

        if self.on_report_due and mono - self._last_report_check >= 60:
            self._last_report_check = mono
            self.on_report_due(now)

    def _add_work(self, app: str, sec: float, now: datetime) -> None:
        self._d_apps[app] += sec
        self._d_work += sec
        self._today_apps[app] = self._today_apps.get(app, 0.0) + sec
        self._today_work += sec
        t = _hms(now)
        if self._d_first is None:
            self._d_first = _hms(now - timedelta(seconds=sec))
        self._d_last = t

    def _add_away(self, sec: float) -> None:
        self._d_away += sec
        self._today_away += sec

    def _commit_pending(self, to_work: bool) -> float:
        total = sum(s for _, s in self._pending)
        if to_work:
            now = datetime.now()
            for app, sec in self._pending:
                self._add_work(app, sec, now)
        elif total:
            self._add_away(total)
        self._pending.clear()
        return total

    def _switch(self, status: str, at: datetime) -> None:
        log.info("Төлөв: %s (%s)", STATUS_TEXT[status], _hms(at))
        if self._period_id is not None:
            self.storage.update_period_end(self._period_id, _hms(at))
        self.status = status
        self._open_period(status, at)
        if self.on_status_change:
            self.on_status_change()

    def _open_period(self, status: str, at: datetime) -> None:
        self._period_id = self.storage.open_period(self._day, status, _hms(at))

    def _flush(self, now: datetime) -> None:
        if self._d_apps or self._d_work or self._d_away:
            self.storage.add_totals(
                self._day, dict(self._d_apps), self._d_work, self._d_away, self._d_first, self._d_last
            )
        self._d_apps.clear()
        self._d_work = self._d_away = 0.0
        self._d_first = self._d_last = None
        if self._period_id is not None:
            self.storage.update_period_end(self._period_id, _hms(now))
        self._last_flush = time.monotonic()
