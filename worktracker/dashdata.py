"""Самбарын өгөгдөл: Supabase-ээс татаж, хүн тус бүрээр нэгтгэнэ.

Хэн юуг харах вэ гэдгийг сервер (RLS) шийднэ: ажилтан өөрийгөө, менежер өөрийн хэлтсийг,
админ бүгдийг. Энд зөвхөн ирсэн өгөгдлийг нэгтгэнэ.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from .supa import SupaClient

OFFLINE_AFTER_SEC = 180  # сүүлийн мэдээллээс хойш ийм удаан бол "Офлайн"
STATUS_TEXT = {"working": "Ажиллаж байна", "away": "Гарсан", "offline": "Офлайн"}


def parse_ts(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def live_status(live: dict | None, now: datetime | None = None) -> str:
    if not live or live.get("status") == "offline":
        return "offline"
    seen = parse_ts(live.get("last_seen_at"))
    now = now or datetime.now(timezone.utc)
    if seen is None or (now - seen).total_seconds() > OFFLINE_AFTER_SEC:
        return "offline"
    return "away" if live.get("status") == "away" else "working"


def ago_text(ts: str | None, now: datetime | None = None) -> str:
    t = parse_ts(ts)
    if t is None:
        return ""
    s = max(0, ((now or datetime.now(timezone.utc)) - t).total_seconds())
    if s < 60:
        return "дөнгөж сая"
    if s < 3600:
        return f"{int(s // 60)} мин"
    if s < 86400:
        return f"{int(s // 3600)}ц {int(s % 3600 // 60)}м"
    return f"{int(s // 86400)} өдөр"


def merge_daily(rows: list[dict]) -> dict:
    """Нэг хүний нэг өдрийн хэд хэдэн компьютерийн мөрийг нэгтгэнэ."""
    out = {"work_seconds": 0, "away_seconds": 0, "first_seen": None, "last_seen": None,
           "apps": [], "periods": [], "hostnames": []}
    apps: dict[str, int] = {}
    for r in rows:
        out["work_seconds"] += r.get("work_seconds") or 0
        out["away_seconds"] += r.get("away_seconds") or 0
        if r.get("first_seen") and (out["first_seen"] is None or r["first_seen"] < out["first_seen"]):
            out["first_seen"] = r["first_seen"]
        if r.get("last_seen") and (out["last_seen"] is None or r["last_seen"] > out["last_seen"]):
            out["last_seen"] = r["last_seen"]
        for a, s in r.get("apps") or []:
            apps[a] = apps.get(a, 0) + int(s)
        out["periods"].extend(r.get("periods") or [])
        if r.get("hostname"):
            out["hostnames"].append(r["hostname"])
    out["apps"] = sorted(apps.items(), key=lambda kv: kv[1], reverse=True)
    out["periods"].sort(key=lambda p: p[1])
    return out


def load(supa: SupaClient, day: str) -> dict:
    """Самбарт хэрэгтэй бүх өгөгдлийг татна."""
    d0 = (date.fromisoformat(day) - timedelta(days=6)).isoformat()
    profiles = supa.select("wt_profiles", "user_id,email,full_name,role,department_id,active,created_at",
                           order="full_name")
    departments = supa.select("wt_departments", "id,name", order="name")
    live = supa.select("wt_live", "user_id,hostname,platform,status,current_app,camera_ok,status_since,last_seen_at")
    daily = supa.select("wt_daily", "user_id,hostname,work_seconds,away_seconds,first_seen,last_seen,apps,periods",
                        day=f"eq.{day}")
    week = supa.select("wt_daily", "user_id,day,work_seconds", day=[f"gte.{d0}", f"lte.{day}"])
    return build(profiles, departments, live, daily, week, day)


def build(profiles, departments, live, daily, week, day: str, now: datetime | None = None) -> dict:
    dept_name = {d["id"]: d["name"] for d in departments}
    live_by = {r["user_id"]: r for r in live}
    daily_by: dict[str, list] = {}
    for r in daily:
        daily_by.setdefault(r["user_id"], []).append(r)
    week_by: dict[str, dict[str, int]] = {}
    for r in week:
        cell = week_by.setdefault(r["user_id"], {})
        cell[r["day"]] = cell.get(r["day"], 0) + (r.get("work_seconds") or 0)

    is_today = day == date.today().isoformat()
    people = []
    for p in profiles:
        if p["role"] == "pending":
            continue
        has_day = p["user_id"] in daily_by
        if not p["active"] and not has_day:
            continue
        lv = live_by.get(p["user_id"])
        people.append({
            "user_id": p["user_id"],
            "name": p["full_name"] or p["email"],
            "email": p["email"],
            "role": p["role"],
            "department": dept_name.get(p["department_id"], ""),
            "department_id": p["department_id"],
            "live": lv,
            "status": live_status(lv, now) if is_today else "offline",
            "day": merge_daily(daily_by.get(p["user_id"], [])),
            "has_day": has_day,
            "week": week_by.get(p["user_id"], {}),
        })
    order = {"working": 0, "away": 1, "offline": 2}
    people.sort(key=lambda x: (order[x["status"]], -x["day"]["work_seconds"], x["name"]))
    days = [(date.fromisoformat(day) - timedelta(days=6 - i)).isoformat() for i in range(7)]
    return {
        "day": day,
        "is_today": is_today,
        "people": people,
        "profiles": profiles,
        "departments": departments,
        "pending": [p for p in profiles if p["role"] == "pending"],
        "week_days": days,
    }


def day_range(period_lists) -> tuple[int, int]:
    """Цагийн шугамын хүрээ (секундээр), хамгийн багадаа 08:00–18:00."""
    lo, hi = 8 * 3600, 18 * 3600
    for periods in period_lists:
        for _, s, e in periods or []:
            lo, hi = min(lo, to_sec(s)), max(hi, to_sec(e))
    return lo // 3600 * 3600, -(-hi // 3600) * 3600


def to_sec(t: str) -> int:
    h, m, *s = (int(x) for x in str(t).split(":"))
    return h * 3600 + m * 60 + (s[0] if s else 0)
