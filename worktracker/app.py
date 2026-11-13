"""Програмыг угсарч ажиллуулна: камер + tracker + тайлан илгээгч + цонх."""
from __future__ import annotations

import logging
import signal
import threading
import time
from datetime import date, datetime, timedelta
from datetime import time as dtime

from .cloud import CloudSync
from .config import Config, data_dir, user_data_dir
from .notify import channels_enabled, is_fully_sent, send_report
from .storage import Storage
from .tracker import Tracker

log = logging.getLogger(__name__)

RETRY_SEC = 600  # илгээлт амжилтгүй бол 10 минутын дараа дахин оролдоно
CATCHUP_DAYS = 7  # өмнөх хэдэн өдрийн илгээгдээгүй тайланг нөхөж илгээх


def _parse_hhmm(s: str) -> dtime:
    h, m = (int(x) for x in str(s).split(":")[:2])
    return dtime(h, m)


class ReportScheduler:
    """Өдөр бүр report_time-д тайлан илгээнэ. Тэр үед компьютер унтарсан байсан бол
    дараа нь асахад өмнөх өдрийн тайланг нөхөж илгээнэ."""

    def __init__(self, cfg: Config, storage: Storage):
        self.cfg = cfg
        self.storage = storage
        self.tracker: Tracker | None = None
        self._busy = threading.Lock()
        self._next_try = 0.0

    def __call__(self, now: datetime) -> None:
        if not channels_enabled(self.cfg) or time.monotonic() < self._next_try:
            return
        today = now.date()
        due_today = now.time() >= _parse_hhmm(self.cfg["report_time"])
        oldest = (today - timedelta(days=CATCHUP_DAYS)).isoformat()
        days = [
            d
            for d in self.storage.days_with_data()
            if d >= oldest
            and (d < today.isoformat() or (d == today.isoformat() and due_today))
            and not is_fully_sent(self.cfg, self.storage, d)
        ]
        if days and self._busy.acquire(blocking=False):
            threading.Thread(target=self._send, args=(days,), name="report", daemon=True).start()

    def _send(self, days: list[str]) -> None:
        try:
            if self.tracker:
                self.tracker.flush_now()
            all_ok = True
            for d in days:
                ok, msg = send_report(self.cfg, self.storage, d, only_unsent=True)
                log.info("Тайлан %s: %s", d, msg)
                all_ok = all_ok and ok
            self._next_try = 0.0 if all_ok else time.monotonic() + RETRY_SEC
        finally:
            self._busy.release()


def storage_path(user_id: str | None):
    """Компьютерийн хэрэглэгч бүр тусдаа өгөгдөлтэй."""
    return (user_data_dir(user_id) if user_id else data_dir()) / "worktime"


class App:
    def __init__(self, cfg: Config, api=None, admin: bool = False):
        from .presence import PresenceMonitor  # OpenCV-г хэрэгтэй үед л ачаална

        self.cfg = cfg
        self.api = api  # ShareClient (админы хуваалцсан хавтас) эсвэл None
        self.storage = Storage(storage_path(api.user_id if api else None))
        self.presence: PresenceMonitor | None
        try:
            self.presence = PresenceMonitor(cfg["camera_index"], cfg["camera_check_interval_sec"], cfg=cfg)
        except Exception:  # noqa: BLE001
            log.exception("Камерын модуль ачаалж чадсангүй")
            self.presence = None
        self.scheduler = ReportScheduler(cfg, self.storage)
        self.tracker = Tracker(cfg, self.storage, self.presence, on_report_due=self.scheduler)
        self.scheduler.tracker = self.tracker
        self.cloud: CloudSync | None = None
        if api is not None:
            self.cloud = CloudSync(cfg, self.storage, self.tracker, api)
            self.tracker.on_status_change = self.cloud.poke
            self.tracker.on_app_change = self.cloud.poke_live
        self.alerts = None
        if admin and api is not None:  # Telegram мэдэгдэл зөвхөн админы компьютер дээрээс
            from .alerts import AlertRunner

            self.alerts = AlertRunner(api)
        self._stopped = False

    def start(self) -> None:
        if self.presence:
            self.presence.start()
        self.tracker.start()
        if self.cloud:
            self.cloud.start()
        if self.alerts:
            self.alerts.start()
        log.info("Эхэллээ. Өгөгдөл: %s", self.storage.path)

    def cloud_status(self) -> str:
        if self.cloud:
            return self.cloud.status_text()
        return "Тохируулаагүй (зөвхөн энэ компьютерт)"

    def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        if self.alerts:
            self.alerts.stop()
        self.tracker.stop()
        if self.cloud:
            self.cloud.stop()  # эцсийн дүн + "Офлайн"-г хавтсанд бичнэ
        if self.presence:
            self.presence.stop()
        self.storage.close()
        log.info("Зогслоо")

    def send_today(self) -> tuple[bool, str]:
        self.tracker.flush_now()
        return send_report(self.cfg, self.storage, date.today().isoformat())

    def run_headless(self) -> None:
        stop = threading.Event()
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: stop.set())
        self.start()
        print("Ажиллаж байна. Зогсоох: Ctrl+C")
        last = None
        while not stop.wait(1):
            s = self.tracker.snapshot()
            line = f"{s['status_text']:<15} | {s['app'] or '-':<25} | ажилласан {int(s['work_seconds'] // 60)} мин"
            if line != last:
                print(line, flush=True)
                last = line
        self.stop()
