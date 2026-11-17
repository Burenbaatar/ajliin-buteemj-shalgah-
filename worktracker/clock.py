"""Програмын цаг — ажилтан компьютерийн цаг, цагийн бүсийг гараар өөрчилсөн ч хуурагдахгүй.

* Цагийн бүс: Windows-ийнхийг ашиглахгүй. UTC цагийг компанийн бүсээр (анхдагч Asia/Ulaanbaatar, UTC+8)
  хөрвүүлнэ — UTC нь цагийн бүс солиход өөрчлөгддөггүй.
* Системийн цаг: програм эхлэхдээ системийн цагийг аваад, дараа нь компьютер асаснаас хойших хугацаагаар
  (унтсан хугацааг оруулаад) өөрөө тоолно. Системийн цаг гэнэт JUMP_SEC-ээс их үсэрвэл (гараар өөрчилсөн)
  шинэ цагийг авахгүй, tracker-т мэдэгдэнэ ("clock" сэжигтэй үе). Windows-ийн автомат тохируулгын жижиг
  засварыг өдөрт нийт DAILY_ADJUST_SEC хүртэл дагана.
* Програмыг хаагаад цагаа ухрааж, дахин асаасныг ч (компьютерийг дахин ачаалаагүй бол) илрүүлнэ.
* Админы компьютер (hub.py) хариу бүртээ өөрийн цагийг өгдөг → sync(). Холбогдсон үед програмын цаг = админы
  компьютерийн цаг. Цагаа ухрааж компьютерээ дахин ачаалсан ч анхны холболтоор цаг засагдаж, өмнөх бичлэгүүд
  ("sync" үйл явдал → tracker) зөв цаг руу шилжинэ.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import date, datetime, timedelta, timezone, tzinfo

log = logging.getLogger(__name__)

DEFAULT_TZ = "Asia/Ulaanbaatar"
# tzdata сан байхгүй үед (Windows) — Монголд зуны цаг байхгүй тул тогтмол зөрүү хангалттай
_FIXED_HOURS = {"Asia/Ulaanbaatar": 8, "Asia/Choibalsan": 8, "Asia/Hovd": 7}

JUMP_SEC = 120.0          # системийн цаг үүнээс их зөрвөл "гараар өөрчилсөн"
ADJUST_MIN_SEC = 1.0      # үүнээс бага зөрүүг (хэмжилтийн шуугиан) тоохгүй
DAILY_ADJUST_SEC = 300.0  # Windows-ийн автомат засварыг өдөрт нийт ийм хэмжээгээр дагана
SERVER_TOL_SEC = 300.0    # админы компьютертэй ийм хүртэлх зөрүү хэвийн (hub.MAX_SKEW_SEC-тэй ижил)
SYNC_MAX_RTT = 5.0        # үүнээс удаан хариу ирвэл цагийг нь тааруулахад ашиглахгүй
SAVE_EVERY_SEC = 30.0
META_KEY = "clock"


def zone(name: str | None = None) -> tzinfo:
    """Компанийн цагийн бүс. Системийн цагийн бүс рүү ХЭЗЭЭ Ч буцахгүй (ажилтан сольж болдог)."""
    name = name or DEFAULT_TZ
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name)
    except Exception:  # noqa: BLE001 — Windows дээр tzdata байхгүй байж болно
        hours = _FIXED_HOURS.get(name)
        if hours is None:
            log.warning("Цагийн бүс %r олдсонгүй — UTC+8 ашиглана", name)
            hours = 8
        return timezone(timedelta(hours=hours), name)


def _make_since_boot():
    """Компьютер асаснаас хойших секунд, УНТСАН хугацааг оруулаад. Системийн цаг өөрчлөхөд нөлөөлөхгүй."""
    return time.monotonic  # QueryPerformanceCounter / GetTickCount64 — хоёулаа унтсан хугацааг оруулдаг


class Clock:
    def __init__(self, tz_name: str | None = None, wall=time.time, since_boot=None):
        self._wall = wall
        self._boot = since_boot or _make_since_boot()
        self._lock = threading.RLock()
        self.tz = zone(tz_name)
        self._anchor_wall = self._wall()
        self._anchor_boot = self._boot()
        # Системийн цаг − програмын цаг = _base (хэвийн зөрүү, админы компьютертэй тулгасны дараа) + _jump
        self._base = 0.0
        self._jump = 0.0  # гараар өөрчилсөн зөрүү (0 = хэвийн)
        self._synced = False
        self._events: list[tuple[str, float]] = []  # sync()-ээс tracker-т дамжуулах
        self._adjust_day: date | None = None
        self._adjusted = 0.0
        self._last_save = float("-inf")

    # ------------------------------------------------------------------ унших
    def timestamp(self) -> float:
        """UTC unix цаг (сек) — системийн цаг өөрчлөгдсөн ч зөв."""
        with self._lock:
            return self._anchor_wall + (self._boot() - self._anchor_boot)

    def now(self) -> datetime:
        """Компанийн цагийн бүсээрх одоогийн цаг (tzinfo-гүй, datetime.now()-ийн оронд)."""
        return datetime.fromtimestamp(self.timestamp(), self.tz).replace(tzinfo=None)

    def today(self) -> date:
        return self.now().date()

    @property
    def tampered(self) -> bool:
        """Системийн цаг гараар өөрчлөгдсөн хэвээр байна (самбарт "clock" сэжигтэй үе)."""
        return bool(self._jump)

    def set_zone(self, name: str | None) -> None:
        tz = zone(name)
        if str(tz) != str(self.tz):
            log.info("Цагийн бүс: %s", tz)
        self.tz = tz

    # ------------------------------------------------------------------ шалгах
    def check(self) -> list[tuple[str, float]]:
        """Секунд тутам (tracker) дуудна. Үйл явдлууд:
        ("jump", зөрүү_сек)  — системийн цагийг шинээр өөрчилсөн (сөрөг = ухраасан, эерэг = урагшлуулсан);
        ("restored", 0)      — системийн цаг хэвийн болсон;
        ("sync", засвар_сек) — админы компьютертэй тулгаж програмын цагийг засав (эерэг = хоцорч байсан)."""
        with self._lock:
            events, self._events = self._events, []
            diff = self._wall() - self.timestamp() - self._base
            day = self.today()
            if day != self._adjust_day:
                self._adjust_day, self._adjusted = day, 0.0
            if abs(diff) <= JUMP_SEC:
                if abs(diff) >= ADJUST_MIN_SEC:
                    if self._synced:
                        self._base += diff  # манай цаг = админых; системийн жижиг засвар нөлөөлөхгүй
                    elif self._adjusted + abs(diff) <= DAILY_ADJUST_SEC:
                        self._anchor_wall += diff  # Windows-ийн автомат тохируулга — системийг дагана
                        self._adjusted += abs(diff)
                if self._jump:
                    self._jump = 0.0
                    log.info("Системийн цаг хэвийн боллоо")
                    events.append(("restored", 0.0))
            elif not self._jump or abs(diff - self._jump) > JUMP_SEC:
                change = diff - self._jump
                self._jump = diff
                log.warning("Системийн цагийг гараар өөрчилсөн: %+.0f сек (програм өөрийн цагаар үргэлжилнэ)", change)
                events.append(("jump", change))
            return events

    def poll(self) -> tuple[list[tuple[str, float]], datetime]:
        """check() + now() нэг агшинд (хооронд нь sync() орж ирэхгүй) — tracker бичлэг бүртээ үүгээр цаг авна."""
        with self._lock:
            return self.check(), self.now()

    def sync(self, server_ts: float, rtt: float = 0.0) -> None:
        """Админы компьютерийн цаг (hub хариуны X-Time + замын хугацааны хагас). Програмын цагийг түүнд тааруулна."""
        if not (0.0 <= rtt <= SYNC_MAX_RTT) or server_ts < 1e9:
            return
        with self._lock:
            diff = server_ts - self.timestamp()
            self._anchor_wall += diff
            dev = self._wall() - self.timestamp()  # системийн цаг − админы цаг
            if self._jump and abs(dev - self._base) > JUMP_SEC:
                self._jump = dev - self._base  # гараар өөрчилсөн хэвээр
            elif abs(dev) > SERVER_TOL_SEC:  # системийн цаг админыхаас хол — програм асахаас өмнө өөрчилсөн
                if not self._jump and abs(diff) <= JUMP_SEC:
                    self._events.append(("jump", dev))
                self._base, self._jump = 0.0, dev
            else:  # системийн цаг зөв — хэвийн зөрүүг санана
                if self._jump:
                    self._events.append(("restored", 0.0))
                self._base, self._jump = dev, 0.0
            if not self._synced:
                log.info("Админы компьютерийн цагтай тулгалаа (зөрүү %+.1f сек)", diff)
            self._synced = True
            if abs(diff) > JUMP_SEC:
                log.warning("Програмын цаг админыхаас %+.0f сек зөрүүтэй байсан — засав", -diff)
                self._events.append(("sync", diff))

    # ------------------------------------------------------------------ хадгалах (дахин эхлэхэд шалгах)
    def attach(self, storage) -> None:
        """Өмнөх ажиллалтын цагтай харьцуулна: програмыг хаагаад цагаа ухрааж дахин асаасан бол өмнөх цагаар
        үргэлжилнэ (дараагийн check() үүнийг "jump" гэж мэдэгдэнэ)."""
        try:
            saved = storage.get_meta(META_KEY) or {}
            wall0, boot0 = float(saved["wall"]), float(saved["boot"])
            base0 = max(-SERVER_TOL_SEC, min(float(saved.get("base") or 0.0), SERVER_TOL_SEC))
        except (KeyError, TypeError, ValueError, AttributeError):
            return
        with self._lock:
            if self._synced:
                return  # админы компьютерийн цаг илүү найдвартай
            boot = self._boot()
            if boot < boot0:
                return  # компьютер дахин ачаалсан — харьцуулах боломжгүй
            expected = wall0 + (boot - boot0)
            dev = self._wall() - expected - base0
            # УХРААСАН эсвэл өөрчлөөгүй бол өмнөх цагаар үргэлжилнэ. Урагшилсан мэт харагдах нь дахин
            # ачаалсных байж болно (boot тоолуур эхнээсээ) — тэр үед системийн цагийг авна.
            if dev < -JUMP_SEC or abs(dev) <= JUMP_SEC:
                self._anchor_wall, self._anchor_boot, self._base = expected, boot, base0

    def save(self, storage, force: bool = False) -> None:
        with self._lock:
            boot = self._boot()
            if not force and boot - self._last_save < SAVE_EVERY_SEC:
                return
            self._last_save = boot
            storage.set_meta(META_KEY, {"wall": round(self.timestamp(), 3), "boot": round(boot, 3),
                                        "base": round(self._base, 3)})


CLOCK = Clock()
