"""Компьютер дээрх өгөгдөл — энгийн JSON файлууд (SQL, нэмэлт сан шаардахгүй).

Бүтэц (хэрэглэгч бүрийн хавтаст):
    worktime/
        meta.json            — device_id, илгээсэн тайлангийн тэмдэг г.м.
        days/2026-11-11.json — нэг өдрийн дүн, апп, үеүд, сэжигтэй үеүд

Файл бүрийг түр файлд бичээд os.replace-ээр сольдог тул компьютер гэнэт унтарсан ч эвдрэхгүй.
"""
from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path

_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _empty_day() -> dict:
    # pauses: "Зогсоох" дарсан үеүд {id, start, end, reason} — админ зөвшөөрвөл ажилласан цагт тооцогдоно
    return {"work_seconds": 0.0, "away_seconds": 0.0, "first_seen": None, "last_seen": None,
            "apps": {}, "periods": [], "flags": [], "clock_jumps": [], "pauses": []}


def shift_hms(t: str, delta_sec: float) -> str:
    """"08:50:00" + 3060 сек → "09:41:00" (өдрийн хүрээнээс гарахгүй)."""
    h, m, s = (int(x) for x in str(t).split(":"))
    sec = max(0, min(86399, h * 3600 + m * 60 + s + int(round(delta_sec))))
    return f"{sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def _read(path: Path, default):
    try:
        with path.open(encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except (OSError, ValueError):
        # Эвдэрсэн файлыг хадгалаад шинээр эхэлнэ
        try:
            os.replace(path, path.with_suffix(".broken"))
        except OSError:
            pass
        return default


def _write(path: Path, data) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, path)


class Storage:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._days_dir = self.path / "days"
        self._days_dir.mkdir(parents=True, exist_ok=True)
        self._meta_path = self.path / "meta.json"
        self._lock = threading.Lock()
        self._meta: dict = _read(self._meta_path, {})
        self._cache: dict[str, dict] = {}

    def close(self) -> None:
        pass  # бичилт бүр шууд дискэнд хадгалагддаг

    # ------------------------------------------------------------------ дотоод
    def _day(self, day: str) -> dict:
        if not _DAY_RE.match(day):
            raise ValueError(f"буруу өдөр: {day}")
        d = self._cache.get(day)
        if d is None:
            d = _empty_day()
            d.update(_read(self._days_dir / f"{day}.json", {}))
            if len(self._cache) > 40:
                self._cache.clear()
            self._cache[day] = d
        return d

    def _save_day(self, day: str) -> None:
        _write(self._days_dir / f"{day}.json", self._cache[day])

    @staticmethod
    def _split_id(item_id: str) -> tuple[str, int]:
        day, idx = item_id.rsplit("#", 1)
        return day, int(idx)

    # ------------------------------------------------------------------ бичих
    def add_totals(
        self,
        day: str,
        apps: dict[str, float],
        work: float,
        away: float,
        first_seen: str | None,
        last_seen: str | None,
    ) -> None:
        with self._lock:
            d = self._day(day)
            for app, sec in apps.items():
                d["apps"][app] = d["apps"].get(app, 0.0) + sec
            d["work_seconds"] += work
            d["away_seconds"] += away
            if first_seen and (d["first_seen"] is None or first_seen < d["first_seen"]):
                d["first_seen"] = first_seen
            if last_seen and (d["last_seen"] is None or last_seen > d["last_seen"]):
                d["last_seen"] = last_seen
            self._save_day(day)

    def _open(self, day: str, key: str, label: str, value: str, start: str) -> str:
        with self._lock:
            d = self._day(day)
            d[key].append({label: value, "start": start, "end": start})
            self._save_day(day)
            return f"{day}#{len(d[key]) - 1}"

    def _close(self, key: str, item_id: str, end: str) -> None:
        day, idx = self._split_id(item_id)
        with self._lock:
            d = self._day(day)
            if 0 <= idx < len(d[key]):
                d[key][idx]["end"] = end
                self._save_day(day)

    def open_period(self, day: str, status: str, start: str) -> str:
        return self._open(day, "periods", "status", status, start)

    def update_period_end(self, period_id: str, end: str) -> None:
        self._close("periods", period_id, end)

    def open_flag(self, day: str, kind: str, start: str) -> str:
        return self._open(day, "flags", "kind", kind, start)

    def update_flag_end(self, flag_id: str, end: str) -> None:
        self._close("flags", flag_id, end)

    def open_pause(self, day: str, pause_id: str, start: str, reason: str) -> str:
        """Ажилтан "Зогсоох" дарж шалтгаанаа бичсэн. pause_id нь админы шийдвэрийг холбоно (цаг шилжсэн ч өөрчлөгдөхгүй)."""
        with self._lock:
            d = self._day(day)
            d["pauses"].append({"id": pause_id, "start": start, "end": start, "reason": reason})
            self._save_day(day)
            return f"{day}#{len(d['pauses']) - 1}"

    def update_pause_end(self, item_id: str, end: str) -> None:
        self._close("pauses", item_id, end)

    def add_clock_jump(self, day: str, at: str, delta_sec: int, kind: str | None = None) -> None:
        """Компьютерийн цаг өөрчлөгдсөн: [цаг (програмын зөв цагаар), хэдэн сек (сөрөг = ухраасан/хоцорсон)].
        kind="server" — админы компьютертэй тулгахад илэрсэн (жишээ нь цагаа ухрааж дахин ачаалсан)."""
        with self._lock:
            d = self._day(day)
            item = [at, int(delta_sec)] + ([kind] if kind else [])
            d["clock_jumps"] = (d.get("clock_jumps") or [])[-99:] + [item]
            self._save_day(day)

    def marks(self, day: str) -> dict:
        """Одоогийн бичлэгийн тоо, ирсэн/явсан цаг — shift_since()-д "эндээс хойш" гэж заана."""
        with self._lock:
            d = self._day(day)
            return {"periods": len(d["periods"]), "flags": len(d["flags"]), "pauses": len(d["pauses"]),
                    "jumps": len(d.get("clock_jumps") or []), "first": d["first_seen"], "last": d["last_seen"]}

    def shift_since(self, day: str, mark: dict, delta_sec: float, first: str | None, last: str | None) -> None:
        """Програмын цагийг админы компьютертэй тулгаж засав: mark-аас хойш бичигдсэн цагуудыг delta-аар
        шилжүүлж, ирсэн/явсан цагийг (tracker тооцоолсон) солино."""
        with self._lock:
            d = self._day(day)
            for key in ("periods", "flags", "pauses"):
                for item in d[key][mark.get(key, 0):]:
                    item["start"], item["end"] = shift_hms(item["start"], delta_sec), shift_hms(item["end"], delta_sec)
            jumps = d.get("clock_jumps") or []
            for j in jumps[min(mark["jumps"], len(jumps)):]:
                j[0] = shift_hms(j[0], delta_sec)
            d["first_seen"], d["last_seen"] = first, last
            self._save_day(day)

    def set_meta(self, key: str, value) -> None:
        with self._lock:
            self._meta[key] = value
            _write(self._meta_path, self._meta)

    # ------------------------------------------------------------------ унших
    def get_meta(self, key: str):
        with self._lock:
            return self._meta.get(key)

    def get_day(self, day: str) -> dict:
        with self._lock:
            d = self._day(day)
            apps = sorted(d["apps"].items(), key=lambda kv: kv[1], reverse=True)
            periods = sorted((dict(p) for p in d["periods"]), key=lambda p: p["start"])
            flags = sorted((dict(f) for f in d["flags"] if f["end"] > f["start"]), key=lambda f: f["start"])
            return {
                "day": day,
                "work_seconds": d["work_seconds"],
                "away_seconds": d["away_seconds"],
                "first_seen": d["first_seen"],
                "last_seen": d["last_seen"],
                "apps": apps,
                "periods": periods,
                "flags": flags,
                "clock_jumps": [list(j) for j in d.get("clock_jumps") or []],
                "pauses": sorted((dict(p) for p in d["pauses"]), key=lambda p: p["start"]),
            }

    def days_with_data(self) -> list[str]:
        out = []
        with self._lock:
            for f in self._days_dir.glob("*.json"):
                day = f.stem
                if _DAY_RE.match(day) and self._day(day)["work_seconds"] > 0:
                    out.append(day)
        return sorted(out)
