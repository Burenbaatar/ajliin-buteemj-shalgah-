"""SQLite өгөгдлийн сан. Нэмэлт сервер шаардлагагүй."""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS app_usage (
    day      TEXT NOT NULL,
    app      TEXT NOT NULL,
    seconds  REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (day, app)
);
CREATE TABLE IF NOT EXISTS daily (
    day           TEXT PRIMARY KEY,
    work_seconds  REAL NOT NULL DEFAULT 0,
    away_seconds  REAL NOT NULL DEFAULT 0,
    first_seen    TEXT,
    last_seen     TEXT
);
-- Төлөв тасралтгүй үргэлжилсэн хугацаа: working / away
CREATE TABLE IF NOT EXISTS periods (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    day     TEXT NOT NULL,
    status  TEXT NOT NULL,
    start   TEXT NOT NULL,
    end     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_periods_day ON periods(day);
-- Сэжигтэй үеүд: static (хөдөлгөөнгүй царай), blocked (камер таглагдсан)
CREATE TABLE IF NOT EXISTS flags (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    day     TEXT NOT NULL,
    kind    TEXT NOT NULL,
    start   TEXT NOT NULL,
    end     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_flags_day ON flags(day);
CREATE TABLE IF NOT EXISTS meta (
    key    TEXT PRIMARY KEY,
    value  TEXT
);
"""


class Storage:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock, self._conn:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

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
        with self._lock, self._conn:
            for app, sec in apps.items():
                self._conn.execute(
                    "INSERT INTO app_usage(day, app, seconds) VALUES(?,?,?) "
                    "ON CONFLICT(day, app) DO UPDATE SET seconds = seconds + excluded.seconds",
                    (day, app, sec),
                )
            self._conn.execute(
                """INSERT INTO daily(day, work_seconds, away_seconds, first_seen, last_seen)
                   VALUES(?,?,?,?,?)
                   ON CONFLICT(day) DO UPDATE SET
                     work_seconds = work_seconds + excluded.work_seconds,
                     away_seconds = away_seconds + excluded.away_seconds,
                     first_seen = COALESCE(MIN(first_seen, excluded.first_seen), first_seen, excluded.first_seen),
                     last_seen  = COALESCE(MAX(last_seen,  excluded.last_seen),  last_seen,  excluded.last_seen)""",
                (day, work, away, first_seen, last_seen),
            )

    def open_period(self, day: str, status: str, start: str) -> int:
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO periods(day, status, start, end) VALUES(?,?,?,?)", (day, status, start, start)
            )
            return int(cur.lastrowid)

    def update_period_end(self, period_id: int, end: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("UPDATE periods SET end = ? WHERE id = ?", (end, period_id))

    def open_flag(self, day: str, kind: str, start: str) -> int:
        with self._lock, self._conn:
            cur = self._conn.execute("INSERT INTO flags(day, kind, start, end) VALUES(?,?,?,?)", (day, kind, start, start))
            return int(cur.lastrowid)

    def update_flag_end(self, flag_id: int, end: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("UPDATE flags SET end = ? WHERE id = ?", (end, flag_id))

    def set_meta(self, key: str, value: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO meta(key, value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    # ------------------------------------------------------------------ унших
    def get_meta(self, key: str) -> str | None:
        with self._lock:
            row = self._conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def get_day(self, day: str) -> dict:
        with self._lock:
            d = self._conn.execute("SELECT * FROM daily WHERE day = ?", (day,)).fetchone()
            apps = self._conn.execute(
                "SELECT app, seconds FROM app_usage WHERE day = ? ORDER BY seconds DESC", (day,)
            ).fetchall()
            periods = self._conn.execute(
                "SELECT status, start, end FROM periods WHERE day = ? ORDER BY start, id", (day,)
            ).fetchall()
            flags = self._conn.execute(
                "SELECT kind, start, end FROM flags WHERE day = ? AND end > start ORDER BY start, id", (day,)
            ).fetchall()
        return {
            "day": day,
            "work_seconds": d["work_seconds"] if d else 0.0,
            "away_seconds": d["away_seconds"] if d else 0.0,
            "first_seen": d["first_seen"] if d else None,
            "last_seen": d["last_seen"] if d else None,
            "apps": [(r["app"], r["seconds"]) for r in apps],
            "periods": [dict(r) for r in periods],
            "flags": [dict(r) for r in flags],
        }

    def days_with_data(self) -> list[str]:
        with self._lock:
            rows = self._conn.execute("SELECT day FROM daily WHERE work_seconds > 0 ORDER BY day").fetchall()
        return [r["day"] for r in rows]
