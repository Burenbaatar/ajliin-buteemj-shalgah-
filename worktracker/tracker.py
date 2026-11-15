"""Гол логик: секунд тутам хүн байгаа эсэх, идэвхтэй аппыг шалгаж цаг тоолно."""
from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta

from . import activity
from .clock import CLOCK, SERVER_TOL_SEC, Clock
from .presence import PresenceMonitor
from .storage import Storage, shift_hms

log = logging.getLogger(__name__)

WORKING = "working"
AWAY = "away"
STATUS_TEXT = {WORKING: "Ажиллаж байна", AWAY: "Ажлаас гарсан"}
PAUSED_TEXT = "Зогсоосон (завсарлага)"

TICK_SEC = 1.0
FLUSH_SEC = 30.0
MAX_GAP_SEC = 10.0  # үүнээс урт завсар = компьютер унтсан; тоолохгүй
APP_SETTLE_SEC = 1.0  # апп солигдоод ийм удаан тогтвортой байвал самбар руу даруй илгээнэ


def _hms(dt: datetime) -> str:
    return dt.strftime("%H:%M:%S")


class Tracker:
    def __init__(self, cfg: dict, storage: Storage, presence: PresenceMonitor | None, on_report_due=None,
                 clock: Clock | None = None):
        self.cfg = cfg
        self.storage = storage
        self.presence = presence
        # Цагийг Windows-оос биш, програмын өөрийн цагаас авна (гараар өөрчлөхөд хуурагдахгүй) — clock.py
        self.clock = clock or CLOCK
        self.clock.attach(storage)
        self._clock_flag_id: str | None = None
        self.on_report_due = on_report_due  # callback(now)
        self.on_status_change = None  # callback() — төлөв солигдоход (самбар руу даруй илгээх)
        self.on_app_change = None  # callback() — идэвхтэй апп солигдож APP_SETTLE_SEC тогтвортой болоход
        self._app_candidate: str | None = None
        self._app_candidate_since = 0.0
        self._app_reported: str | None = None

        self.status = WORKING
        self.paused = False  # ажилтан "Зогсоох" дарсан: дахин "Үргэлжлүүлэх" дартал гарсанд тооцно
        self.current_app: str | None = None
        self.camera_ok = False
        self.face_visible = False
        self.camera_state = "none"  # ok | blocked | static | none
        self._flag_id: int | None = None
        self._flag_kind: str | None = None

        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

        now = self.clock.now()
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
        self._mark_session()

    # ------------------------------------------------------------------ public
    def start(self) -> None:
        with self._lock:
            self._open_period(WORKING, self._poll())
        self._thread = threading.Thread(target=self._run, name="tracker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        with self._lock:
            self._commit_pending(to_work=False)  # баталгаажаагүй хугацааг ажилд тооцохгүй
            self._flush(self._poll())
            self.clock.save(self.storage, force=True)

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
                "status_text": PAUSED_TEXT if self.paused else STATUS_TEXT[self.status],
                "paused": self.paused,
                "app": self.current_app,
                "work_seconds": self._today_work + pending,
                "away_seconds": self._today_away,
                "camera_ok": self.camera_ok,
                "face_visible": self.face_visible,
                "camera_state": self.camera_state,
                "top_apps": top,
            }

    def flush_now(self) -> None:
        with self._lock:
            self._flush(self._poll())

    def set_paused(self, paused: bool) -> None:
        """Гараар зогсоох / үргэлжлүүлэх. Зогссон хугацаа "гарсан"-д тооцогдоно (ажилласан цаг нэмэгдэхгүй)."""
        with self._lock:
            if paused == self.paused:
                return
            now = self._poll()
            self.paused = paused
            if paused:
                pending = self._commit_pending(to_work=False)
                if self.status == WORKING:
                    self._switch(AWAY, now - timedelta(seconds=pending))
            else:
                self._last_evidence = time.monotonic()  # дараагийн шалгалт хүн байгаа эсэхийг шийднэ
            log.info("Гараар %s", "зогсоолоо" if paused else "үргэлжлүүллээ")
        if self.presence:
            self.presence.set_paused(paused)  # зогсоосон үед камерыг бүрэн хаана (гэрэл унтарна)
        if self.on_status_change:
            self.on_status_change()

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

        with self._lock:
            now = self._poll()

            # Компьютер унтаад сэрсэн: завсрыг тоолохгүй, шинэ үе эхлүүлнэ
            if dt > MAX_GAP_SEC:
                log.info("%.0f сек завсар (унтсан?) — тоолохгүй", dt)
                self._commit_pending(to_work=False)
                self._flush(now - timedelta(seconds=dt))
                self._flag_id = self._flag_kind = None
                if now.date().isoformat() != self._day:
                    self._day = now.date().isoformat()
                    self._load_today()
                    self._mark_session()
                    self._reopen_clock_flag(now)
                self._last_evidence = mono
                self.status = AWAY if self.paused else WORKING
                self._open_period(self.status, now)
                return

            # Шөнө дунд өдөр солигдох
            day = now.date().isoformat()
            if day != self._day:
                self._commit_pending(to_work=False)
                self._flush(now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(seconds=1))
                self._flag_id = self._flag_kind = None
                self._day = day
                self._load_today()
                self._mark_session()
                self._open_period(self.status, now.replace(hour=0, minute=0, second=0, microsecond=0))
                self._reopen_clock_flag(now.replace(hour=0, minute=0, second=0, microsecond=0))

            # --- Мэдрэгчүүд
            locked = activity.is_screen_locked()
            app = None if locked else activity.get_active_app()
            idle = activity.get_idle_seconds()
            self.current_app = app
            app_changed = not locked and not self.paused and self._app_settled(app, mono)

            cam_ok = bool(self.presence and self.presence.camera_ok)
            self.camera_ok = cam_ok
            self.camera_state = self.presence.camera_state if self.presence else "none"
            self._set_flag(self.camera_state if self.camera_state in ("static", "blocked") else None, now)
            fresh = max(3.0, 2.5 * float(cfg["camera_check_interval_sec"]))
            face = cam_ok and (mono - self.presence.last_face_time) <= fresh
            self.face_visible = face
            input_recent = idle is not None and idle <= float(cfg["input_presence_window_sec"])

            if locked or self.paused:
                evidence = False
            elif cam_ok:
                # A static face may be a photo. Do not let recent keyboard or
                # mouse activity override the liveness warning.
                evidence = self.camera_state != "static" and (
                    face or (bool(cfg["input_counts_as_presence"]) and input_recent)
                )
            elif idle is not None:
                evidence = idle <= float(cfg["no_camera_idle_timeout_sec"])
            else:
                evidence = True  # шалгах арга алга: ажиллаж байна гэж үзнэ

            app_key = app or activity.UNKNOWN_APP
            grace = float(cfg["away_grace_sec"])

            if evidence:
                self._last_evidence = mono
                self._commit_pending(to_work=True, now=now)
                self._add_work(app_key, dt, now)
                if self.status != WORKING:
                    self._switch(WORKING, now)
            elif self.status == WORKING and not locked and not self.paused and (mono - self._last_evidence) <= grace:
                # Түр харагдахгүй байна — шийдвэрийг хойшлуулна
                self._pending.append((app_key, dt))
            else:
                if self.status == WORKING:
                    pending = self._commit_pending(to_work=False)
                    self._switch(AWAY, now - timedelta(seconds=pending))
                self._add_away(dt)

            if mono - self._last_flush >= FLUSH_SEC:
                self._flush(now)

        if app_changed and self.on_app_change:
            self.on_app_change()
        if self.on_report_due and mono - self._last_report_check >= 60:
            self._last_report_check = mono
            self.on_report_due(now)

    def _app_settled(self, app: str | None, mono: float) -> bool:
        """Шинэ апп APP_SETTLE_SEC тогтвортой байвал нэг удаа True. Alt+Tab-аар хурдан гүйлгэхийг шүүнэ."""
        if app != self._app_candidate:
            self._app_candidate, self._app_candidate_since = app, mono
            return False
        if app != self._app_reported and mono - self._app_candidate_since >= APP_SETTLE_SEC:
            self._app_reported = app
            return True
        return False

    def _add_work(self, app: str, sec: float, now: datetime) -> None:
        self._d_apps[app] += sec
        self._d_work += sec
        self._today_apps[app] = self._today_apps.get(app, 0.0) + sec
        self._today_work += sec
        t = _hms(now)
        if self._d_first is None:
            self._d_first = _hms(now - timedelta(seconds=sec))
        if self._s_first is None:
            self._s_first = self._d_first
        self._d_last = self._s_last = t

    def _add_away(self, sec: float) -> None:
        self._d_away += sec
        self._today_away += sec

    def _commit_pending(self, to_work: bool, now: datetime | None = None) -> float:
        total = sum(s for _, s in self._pending)
        if to_work:
            now = now or self.clock.now()
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

    def _set_flag(self, kind: str | None, now: datetime) -> None:
        """Сэжигтэй үе (хөдөлгөөнгүй царай / таглагдсан камер) эхэлсэн, дууссаныг бүртгэнэ."""
        if kind == self._flag_kind:
            return
        if self._flag_id is not None:
            self.storage.update_flag_end(self._flag_id, _hms(now))
        self._flag_id = self.storage.open_flag(self._day, kind, _hms(now)) if kind else None
        self._flag_kind = kind
        log.info("Камерын төлөв: %s", kind or "ok")
        if self.on_status_change:
            self.on_status_change()

    # ------------------------------------------------------------------ програмын цаг
    def _poll(self) -> datetime:
        """self._lock дотор: програмын цагийн үйл явдлыг боловсруулаад одоогийн цагийг өгнө. Бичлэг бүр
        үүгээр цаг авна — тэгснээр цаг засагдахаас өмнөх бичлэгийг л шилжүүлнэ."""
        events, now = self.clock.poll()
        if events:
            self._on_clock_events(events, now)
        self._update_clock_flag(now)
        return now

    def _on_clock_events(self, events: list[tuple[str, float]], now: datetime) -> None:
        """Цаг тоолох нь програмын өөрийн цагаар зөв үргэлжилнэ — энд самбарт ⚠ харагдахаар бүртгэнэ."""
        fix = sum(change for kind, change in events if kind == "sync")
        if fix:
            self._rebase(fix, now)
            if abs(fix) > SERVER_TOL_SEC:  # бага зөрүү нь админы компьютерийн цаг бага зэрэг буруу байж болно
                self.storage.add_clock_jump(self._day, _hms(now), -int(round(fix)), "server")
        for kind, change in events:
            if kind == "jump":
                self.storage.add_clock_jump(self._day, _hms(now), int(round(change)))
        if self.on_status_change:
            self.on_status_change()

    def _update_clock_flag(self, now: datetime) -> None:
        """Системийн цаг өөрчлөгдсөн хэвээр байх хугацааг "clock" сэжигтэй үе болгож бүртгэнэ."""
        if self.clock.tampered and self._clock_flag_id is None:
            self._clock_flag_id = self.storage.open_flag(self._day, "clock", _hms(now))
        elif not self.clock.tampered and self._clock_flag_id is not None:
            self.storage.update_flag_end(self._clock_flag_id, _hms(now))
            self._clock_flag_id = None

    def _mark_session(self) -> None:
        """Энэ цэгээс хойшхи бичлэгүүд програмын одоогийн цагаар хийгдэнэ (_rebase засах хүрээ)."""
        self._s_mark = self.storage.marks(self._day)
        self._s_first: str | None = None
        self._s_last: str | None = None

    def _rebase(self, fix: float, now: datetime) -> None:
        """Програмын цагийг админы компьютертэй тулгаж fix сек засав → өмнө нь бичсэн цагуудыг шилжүүлнэ
        (жишээ нь цагаа 51 мин ухрааж дахин ачаалсан: ирсэн цаг 08:50 → 09:41)."""
        self._flush(now - timedelta(seconds=fix))  # хадгалаагүйг хуучин цагаар нь бичээд, хамтад нь шилжүүлнэ
        m = self._s_mark
        first = shift_hms(self._s_first, fix) if self._s_first else None
        last = shift_hms(self._s_last, fix) if self._s_last else None
        first = min(x for x in (m["first"], first) if x) if (m["first"] or first) else None
        last = max(x for x in (m["last"], last) if x) if (m["last"] or last) else None
        self.storage.shift_since(self._day, m, fix, first, last)
        log.info("Өнөөдрийн бичлэгийг %+.0f сек шилжүүлэв (ирсэн %s → %s)", fix, self._s_first, first)
        self._mark_session()

    def _reopen_clock_flag(self, at: datetime) -> None:
        """Өдөр солигдоход цаг өөрчлөгдсөн хэвээр байвал шинэ өдөрт үргэлжлүүлнэ."""
        if self._clock_flag_id is not None:
            self._clock_flag_id = self.storage.open_flag(self._day, "clock", _hms(at))

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
        if self._flag_id is not None:
            self.storage.update_flag_end(self._flag_id, _hms(now))
        if self._clock_flag_id is not None:
            self.storage.update_flag_end(self._clock_flag_id, _hms(now))
        self.clock.save(self.storage)
        self._last_flush = time.monotonic()
