"""Telegram мэдэгдэл — зөвхөн админы компьютер дээр, 5 минут тутам хавтаснаас шалгана.

⏰ ирээгүй · 🚶 удаан гарсан · ⚠️ ажлын цагаар програм хаалттай · 📷 камер таглагдсан · 🖼 хөдөлгөөнгүй царай
· 👤 өөр хүн суусан (царай таних асаалттай бол)
· 🕐 компьютерийн цагийг гараар өөрчилсөн / админы компьютерт ирсэн цагаасаа удаан холбогдоогүй
· ⏸ ажилтан түр зогсоож шалтгаанаа бичсэн (админ самбарын «Түр зогсолт» табаас шийднэ).
Нэг үйл явдлыг нэг л удаа илгээнэ (илгээсэн түүх: data_dir()/alerts_log.json).
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timedelta, timezone

from .clock import zone
from .config import data_dir, load_admin
from .dashdata import clock_jump_text, server_gaps

log = logging.getLogger(__name__)

EVERY_SEC = 300


def _parse(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _hm(value, default: str | None = None):
    v = value if value not in (None, "") else default
    if v in (None, ""):
        return None
    try:
        return datetime.strptime(str(v)[:5], "%H:%M").time()
    except ValueError:
        return None


def _zone(name: str | None):
    return zone(name)  # ажилтнуудын програмтай ижил цагийн бүс (системийнх биш)


def _minutes(s: dict, key: str, default: int) -> int:
    v = s.get(key)
    try:
        return int(v) if v not in (None, "") else default
    except (TypeError, ValueError):
        return default


def collect(profiles, departments, live, today_rows, settings: dict, sent: dict,
            now: datetime | None = None, uptime=None, decisions=None) -> list[tuple[str | None, str]]:
    """Илгээх мессежүүдийг [(chat_id, текст)] буцаана. `sent`-д илгээснийг тэмдэглэнэ."""
    s = settings or {}
    now = now or datetime.now(timezone.utc)
    tz = _zone(s.get("timezone") or "Asia/Ulaanbaatar")
    local = now.astimezone(tz)
    today, t = local.date(), local.time()
    ws, we = _hm(s.get("work_start"), "09:00"), _hm(s.get("work_end"), "18:00")
    ls, le = _hm(s.get("lunch_start")), _hm(s.get("lunch_end"))
    if today.isoweekday() not in (s.get("workdays") or [1, 2, 3, 4, 5]) or t < ws or t > we:
        return []
    in_lunch = bool(ls and le and ls <= t < le)
    late_min = _minutes(s, "alert_late_min", 30)
    away_min = _minutes(s, "alert_away_min", 40)
    off_min = _minutes(s, "alert_offline_min", 15)

    prof_by = {p["user_id"]: p for p in profiles if p.get("active", True)}
    dept_chat = {d["id"]: d.get("telegram_chat_id") for d in departments}
    admin_chat = s.get("telegram_admin_chat") or None
    out: list[tuple[str | None, str]] = []

    def name(uid):
        p = prof_by.get(uid) or {}
        return p.get("full_name") or uid

    def local_hm(ts):
        d = _parse(ts)
        return d.astimezone(tz).strftime("%H:%M") if d else "?"

    def alert(uid, kind, key, text):
        k = f"{uid}|{kind}|{key}"
        if k in sent:
            return
        sent[k] = now.isoformat(timespec="seconds")
        chat = dept_chat.get((prof_by.get(uid) or {}).get("department_id"))
        out.append((chat, text))
        if admin_chat != chat:
            out.append((admin_chat, text))

    # 1) Ирээгүй
    late_at = (datetime.combine(today, ws) + timedelta(minutes=late_min)).time()
    if t >= late_at:
        worked = {r["user_id"] for r in today_rows if (r.get("work_seconds") or 0) > 0}
        for uid, p in prof_by.items():
            created = _parse(p.get("created_at"))
            if uid in worked or (created and created.astimezone(tz).date() >= today):
                continue
            alert(uid, "not_arrived", today.isoformat(), f"⏰ {name(uid)} {late_at.strftime('%H:%M')} болтол ирээгүй байна")

    for lv in live:
        uid = lv["user_id"]
        seen = _parse(lv.get("last_seen_at"))
        if uid not in prof_by or not seen:
            continue
        fresh = now - seen < timedelta(minutes=3)
        since = _parse(lv.get("status_since"))
        # 2) Удаан гарсан (цайны цагаас бусад үед)
        if not in_lunch and fresh and lv.get("status") == "away" and since and now - since > timedelta(minutes=away_min):
            alert(uid, "long_away", lv["status_since"],
                  f"🚶 {name(uid)} {local_hm(lv['status_since'])}-с хойш ({away_min}+ минут) ажлын байрандаа алга")
        # 3) Ажлын цагаар програм хаагдсан / офлайн
        seen_l = seen.astimezone(tz)
        if now - seen > timedelta(minutes=off_min) and seen_l.date() == today \
                and ws <= seen_l.time() <= we and not (in_lunch and seen_l.time() >= ls):
            alert(uid, "offline", lv["last_seen_at"],
                  f"⚠️ {name(uid)}-ийн програм {local_hm(lv['last_seen_at'])}-с хойш хаалттай/офлайн байна")
        # 4) Камер таглагдсан / хөдөлгөөнгүй царай
        cam = lv.get("camera_state")
        if fresh and cam in ("blocked", "static", "stranger"):
            when = local_hm(lv.get("camera_state_since"))
            text = {"blocked": f"📷 {name(uid)}-ийн камер таглагдсан байна ({when}-с)",
                    "static": f"🖼 {name(uid)}: камерт сэжигтэй дүрс (зураг/видео?) {when}-с",
                    "stranger": f"👤 {name(uid)}-ийн компьютер дээр өөр хүн сууж байна ({when}-с)"}[cam]
            alert(uid, f"camera_{cam}", lv.get("camera_state_since") or today.isoformat(), text)

    # 5) Компьютерийн цагийг гараар өөрчилсөн (цаг тоолоход нөлөөлөөгүй ч санаатай үйлдэл)
    for r in today_rows:
        uid = r.get("user_id")
        if uid not in prof_by:
            continue
        for at, delta, *kind in r.get("clock_jumps") or []:
            if kind and kind[0] == "server":
                text = (f"🕐 {name(uid)}-ийн компьютерийн цаг {clock_jump_text(delta, 'server')} "
                        f"({str(at)[:5]}-д илэрч, програм зассан)")
            else:
                text = f"🕐 {name(uid)} {str(at)[:5]}-д компьютерийнхээ цагийг {clock_jump_text(delta)}"
            alert(uid, "clock", f"{today.isoformat()}|{r.get('hostname', '')}|{at}", text)
        # 6) Програмын бүртгэлээр эрт ирсэн ч админы компьютерт (асаалттай байхад) удаан холбогдоогүй
        for g in server_gaps([r], uptime):
            alert(uid, "server_gap", f"{today.isoformat()}|{g['hostname']}",
                  f"🕐 {name(uid)}: програм {g['first'][:5]}-д ирсэн гэж бүртгэсэн ч админы компьютерт "
                  f"{g['server'][:5]}-д анх холбогдсон (сүлжээ тасарсан эсвэл цагаа өөрчилсөн байж болно)")
        # 7) Түр зогсоож шалтгаанаа бичсэн — админ хараахан шийдээгүй
        decided = (decisions or {}).get(uid) or {}
        for pz in r.get("pauses") or []:
            if not isinstance(pz, list) or len(pz) != 4 or pz[0] in decided:
                continue
            alert(uid, "pause", f"{today.isoformat()}|{pz[0]}",
                  f"⏸ {name(uid)} {str(pz[1])[:5]}-д түр зогсоолоо: «{pz[3] or 'шалтгаангүй'}»\n"
                  "Самбар → «Түр зогсолт» табаас зөвшөөрөх / татгалзана уу")

    cutoff = (now - timedelta(days=60)).isoformat(timespec="seconds")
    for k in [k for k, v in sent.items() if v < cutoff]:
        del sent[k]
    return out


class AlertRunner:
    """Админы компьютер дээр арын thread-д 5 минут тутам ажиллана."""

    def __init__(self, share):
        self.share = share
        self.path = data_dir() / "alerts_log.json"
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="alerts", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.wait(EVERY_SEC):
            try:
                n = self.run_once()
                if n:
                    log.info("Telegram мэдэгдэл: %d", n)
            except Exception as exc:  # noqa: BLE001
                log.info("Мэдэгдэл шалгаж чадсангүй: %s", getattr(exc, "friendly", exc))

    def run_once(self, now: datetime | None = None, send=None) -> int:
        from .notify import tg_send

        token = (load_admin() or {}).get("telegram_bot_token")
        settings = self.share.company()["settings"]
        if not token or not settings.get("alerts_enabled"):
            return 0
        today = (now or datetime.now(timezone.utc)).astimezone(_zone(settings.get("timezone"))).date().isoformat()
        try:
            sent = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            sent = {}
        uptime = next((r["spans"] for r in self.share.select("wt_hub_uptime", day=f"eq.{today}")), [])
        decisions = {r["user_id"]: r["decisions"] for r in self.share.select("wt_pause_decisions")}
        msgs = collect(self.share.select("wt_profiles"), self.share.select("wt_departments"),
                       self.share.select("wt_live"), self.share.select("wt_daily", day=f"eq.{today}"),
                       settings, sent, now, uptime=uptime, decisions=decisions)
        self.path.write_text(json.dumps(sent, ensure_ascii=False), encoding="utf-8")
        send = send or (lambda chat, text: tg_send(token, chat, text))
        for chat, text in msgs:
            if chat:
                try:
                    send(chat, text)
                except Exception as exc:  # noqa: BLE001
                    log.warning("Telegram илгээж чадсангүй: %s", exc)
        return len({text for _, text in msgs})
