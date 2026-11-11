"""Самбарын өгөгдөл: админы хуваалцсан хавтаснаас уншиж, хүн тус бүрээр нэгтгэж, хуваарь/бүтээмжийг тооцно."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from .share import ShareClient

OFFLINE_AFTER_SEC = 180  # сүүлийн мэдээллээс хойш ийм удаан бол "Офлайн"
STATUS_TEXT = {"working": "Ажиллаж байна", "away": "Гарсан", "offline": "Офлайн"}
FLAG_TEXT = {"static": "Хөдөлгөөнгүй царай (зураг?)", "blocked": "Камер таглагдсан"}
CAMERA_TEXT = {"ok": "OK", "none": "олдсонгүй", "blocked": "таглагдсан", "static": "хөдөлгөөнгүй царай"}

DEFAULT_SETTINGS = {
    "work_start": "09:00", "work_end": "18:00", "lunch_start": "13:00", "lunch_end": "14:00",
    "workdays": [1, 2, 3, 4, 5], "late_grace_min": 5,
    "app_productive": [], "app_unproductive": [],
}


# ---------------------------------------------------------------------------- туслах
def parse_ts(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def to_sec(t: str) -> int:
    h, m, *s = (int(x) for x in str(t).split(":"))
    return h * 3600 + m * 60 + (s[0] if s else 0)


def hm_sec(v) -> int | None:
    try:
        return to_sec(str(v)) if v not in (None, "") else None
    except ValueError:
        return None


def overlap(a0: int, a1: int, b0: int | None, b1: int | None) -> int:
    if b0 is None or b1 is None:
        return 0
    return max(0, min(a1, b1) - max(a0, b0))


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


def settings_with_defaults(s: dict | None) -> dict:
    out = dict(DEFAULT_SETTINGS)
    out.update({k: v for k, v in (s or {}).items() if v is not None})
    return out


# ---------------------------------------------------------------------------- нэгтгэх
def merge_daily(rows: list[dict]) -> dict:
    """Нэг хүний нэг өдрийн хэд хэдэн компьютерийн мөрийг нэгтгэнэ."""
    out = {"work_seconds": 0, "away_seconds": 0, "first_seen": None, "last_seen": None,
           "apps": [], "periods": [], "flags": [], "hostnames": []}
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
        out["flags"].extend(r.get("flags") or [])
        if r.get("hostname"):
            out["hostnames"].append(r["hostname"])
    out["apps"] = sorted(apps.items(), key=lambda kv: kv[1], reverse=True)
    out["periods"].sort(key=lambda p: p[1])
    out["flags"].sort(key=lambda p: p[1])
    return out


# ---------------------------------------------------------------------------- бүтээмж
def categorize(app: str, s: dict) -> str:
    low = app.lower()
    for key, cat in (("app_productive", "productive"), ("app_unproductive", "unproductive")):
        if any(k.strip() and k.strip().lower() in low for k in s.get(key) or []):
            return cat
    return "neutral"


def productivity(apps, s: dict) -> dict:
    out = {"productive": 0, "unproductive": 0, "neutral": 0}
    for a, sec in apps:
        out[categorize(a, s)] += sec
    total = sum(out.values())
    out["pct"] = round(out["productive"] / total * 100) if total and (s.get("app_productive")) else None
    out["unproductive_pct"] = round(out["unproductive"] / total * 100) if total else 0
    return out


# ---------------------------------------------------------------------------- хуваарь
def schedule_metrics(d: dict, has_day: bool, s: dict, day_iso: str, now: datetime | None = None) -> dict:
    """Хоцролт, эрт явалт, илүү цаг, програм хаалттай үе, цайны цаг хассан гарсан хугацаа."""
    now = now or datetime.now()
    day = date.fromisoformat(day_iso)
    is_today = day == now.date()
    now_sec = now.hour * 3600 + now.minute * 60 + now.second
    ws, we = hm_sec(s.get("work_start")) or 9 * 3600, hm_sec(s.get("work_end")) or 18 * 3600
    ls, le = hm_sec(s.get("lunch_start")), hm_sec(s.get("lunch_end"))
    workday = day.isoweekday() in set(s.get("workdays") or [])
    grace = int(s.get("late_grace_min", 5)) * 60
    periods = [(st, to_sec(a), to_sec(b)) for st, a, b in d["periods"] if to_sec(b) > to_sec(a)]
    first = hm_sec(d["first_seen"]) if d["first_seen"] else None
    last = hm_sec(d["last_seen"]) if d["last_seen"] else None
    day_over = not is_today or now_sec >= we

    late = (first - ws) if workday and first is not None and first - ws > grace else 0
    early = (we - last) if workday and last is not None and day_over and we - last > grace else 0
    working = [(a, b) for st, a, b in periods if st == "working"]
    if workday:
        overtime = sum(overlap(a, b, 0, ws) + overlap(a, b, we, 86400) for a, b in working)
    else:
        overtime = sum(b - a for a, b in working)
    lunch_away = sum(overlap(a, b, ls, le) for st, a, b in periods if st == "away") if ls and le else 0

    # Програм ажиллаагүй (хаалттай / компьютер унтраалттай) үе: ирсэн–явсан хооронд, ажлын цагт
    closed = 0
    if workday and periods:
        spans = []
        for _, a, b in sorted(periods, key=lambda p: p[1]):
            if spans and a <= spans[-1][1] + 60:
                spans[-1][1] = max(spans[-1][1], b)
            else:
                spans.append([a, b])
        gaps = [(spans[i][1], spans[i + 1][0]) for i in range(len(spans) - 1)]
        if is_today and not day_over and now_sec - spans[-1][1] > OFFLINE_AFTER_SEC:
            gaps.append((spans[-1][1], now_sec))  # одоо хаалттай байна
        for a, b in gaps:
            g = overlap(a, b, ws, we) - (overlap(max(a, ws), min(b, we), ls, le) if ls and le else 0)
            if g >= 60:
                closed += g
    absent = workday and not has_day and day <= now.date() and (not is_today or now_sec > ws + 3600)
    return {"workday": workday, "late": late, "early": early, "overtime": overtime, "closed": closed,
            "lunch_away": lunch_away, "away_net": max(0, d["away_seconds"] - lunch_away), "absent": absent}


def flag_totals(flags) -> dict:
    out = {"static": 0, "blocked": 0}
    for kind, a, b in flags or []:
        if kind in out:
            out[kind] += max(0, to_sec(b) - to_sec(a))
    return out


# ---------------------------------------------------------------------------- татах
def load(api: ShareClient, day: str) -> dict:
    """Самбарт хэрэгтэй бүх өгөгдлийг татна."""
    d0 = (date.fromisoformat(day) - timedelta(days=6)).isoformat()
    profiles = api.select("wt_profiles", "user_id,email,full_name,department_id,active,created_at",
                           order="full_name")
    departments = api.select("wt_departments", "id,name,telegram_chat_id", order="name")
    live = api.select("wt_live", "user_id,hostname,platform,status,current_app,camera_ok,camera_state,"
                                  "camera_state_since,status_since,last_seen_at")
    daily = api.select("wt_daily", "user_id,hostname,work_seconds,away_seconds,first_seen,last_seen,apps,periods,"
                                    "flags", day=f"eq.{day}")
    week = api.select("wt_daily", "user_id,day,work_seconds", day=[f"gte.{d0}", f"lte.{day}"])
    company = api.select("wt_company", "settings", id="eq.1")
    settings = (company[0].get("settings") if company else None) or {}
    return build(profiles, departments, live, daily, week, day, settings=settings)


def build(profiles, departments, live, daily, week, day: str, now: datetime | None = None,
          settings: dict | None = None) -> dict:
    s = settings_with_defaults(settings)
    now_local = now.astimezone().replace(tzinfo=None) if now and now.tzinfo else (now or datetime.now())
    dept_name = {d["id"]: d["name"] for d in departments}
    live_by = {r["user_id"]: r for r in live}
    daily_by: dict[str, list] = {}
    for r in daily:
        daily_by.setdefault(r["user_id"], []).append(r)
    week_by: dict[str, dict[str, int]] = {}
    for r in week:
        cell = week_by.setdefault(r["user_id"], {})
        cell[r["day"]] = cell.get(r["day"], 0) + (r.get("work_seconds") or 0)
    days = [(date.fromisoformat(day) - timedelta(days=6 - i)).isoformat() for i in range(7)]

    is_today = day == date.today().isoformat()
    people = []
    for p in profiles:
        has_day = p["user_id"] in daily_by
        if not p["active"] and not has_day:
            continue
        created = (p.get("created_at") or "")[:10]
        if created and created > day and not has_day:
            continue  # тухайн өдөр бүртгүүлээгүй байсан
        lv = live_by.get(p["user_id"])
        d = merge_daily(daily_by.get(p["user_id"], []))
        wk = week_by.get(p["user_id"], {})
        absent_days = [dd for dd in days if dd not in wk and (not created or dd >= created)
                       and schedule_metrics(merge_daily([]), False, s, dd, now_local)["absent"]]
        people.append({
            "user_id": p["user_id"],
            "name": p["full_name"] or p["email"],
            "email": p["email"],
            "department": dept_name.get(p["department_id"], ""),
            "department_id": p["department_id"],
            "live": lv,
            "status": live_status(lv, now) if is_today else "offline",
            "camera_state": (lv or {}).get("camera_state") if is_today and live_status(lv, now) != "offline" else None,
            "day": d,
            "has_day": has_day,
            "week": wk,
            "week_absent": absent_days,
            "metrics": schedule_metrics(d, has_day, s, day, now_local),
            "prod": productivity(d["apps"], s),
            "flag_sec": flag_totals(d["flags"]),
        })
    order = {"working": 0, "away": 1, "offline": 2}
    people.sort(key=lambda x: (order[x["status"]], -x["day"]["work_seconds"], x["name"]))
    return {
        "day": day,
        "is_today": is_today,
        "people": people,
        "profiles": profiles,
        "departments": departments,
        "week_days": days,
        "settings": s,
    }


def day_range(period_lists, s: dict | None = None) -> tuple[int, int]:
    """Цагийн шугамын хүрээ (секундээр): ажлын цаг + бүх үе (хамгийн багадаа 08:00–18:00)."""
    lo, hi = 8 * 3600, 18 * 3600
    if s:
        lo = min(lo, hm_sec(s.get("work_start")) or lo)
        hi = max(hi, hm_sec(s.get("work_end")) or hi)
    for periods in period_lists:
        for _, a, b in periods or []:
            lo, hi = min(lo, to_sec(a)), max(hi, to_sec(b))
    return lo // 3600 * 3600, min(86400, -(-hi // 3600) * 3600)
