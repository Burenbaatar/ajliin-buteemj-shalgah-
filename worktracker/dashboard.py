"""Админы самбар: ажилтнууд, 7 хоног, хэрэглэгчид, хэлтэс, тохиргоо.

Зөвхөн админы компьютер дээр, админ нууц үгээр нээгдэнэ. Өгөгдлийг админы хуваалцсан хавтаснаас (share.py) уншина.
"""
from __future__ import annotations

import csv
import json
import tkinter as tk
from datetime import date, datetime, timedelta
from tkinter import filedialog, messagebox, ttk

from . import APP_TITLE
from . import dashdata as dd
from .share import ShareClient
from .uiutil import (BG, BORDER, FG, FONT, GAP, GREEN, MUTED, PENDING_BG, RED, STATUS_COLOR, SURFACE, Bg,
                     bind_wheel, button, fmt_dur, fmt_hm, friendly)

REFRESH_MS = 30000  # бүтэн шинэчлэлт (өдрийн дүн, 7 хоног)
LIVE_MS = 2000  # одоогийн төлөв, апп — ажилтан апп солиход самбарт ≤ 5 сек-д харагдана
ROW_H = 44
COLS = [("Ажилтан", 200), ("Одоо", 175), ("Одоогийн апп", 150), ("Ажилласан", 85), ("Гарсан", 75),
        ("Ирсэн – Явсан", 105), ("Хоцорсон", 80), ("Бүтээмж", 75), ("Өдрийн явц", 240)]
ORANGE = "#bc4c00"
LUNCH = "#d8dee4"
CAT_COLOR = {"productive": "#1a7f37", "unproductive": "#cf222e", "neutral": "#8c959f"}
CAT_TEXT = {"productive": "бүтээмжтэй", "unproductive": "бүтээмжгүй", "neutral": "бусад"}
WEEKDAYS = ["Да", "Мя", "Лх", "Пү", "Ба", "Бя", "Ня"]


def _mix(hex_a: str, hex_b: str, t: float) -> str:
    a = [int(hex_a[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(hex_b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(a, b))


def draw_timeline(cv: tk.Canvas, x: int, y: int, w: int, h: int, periods, rng, settings=None, flags=None,
                  tags=()) -> None:
    """Өдрийн явц: ногоон = ажилласан, улаан = гарсан, саарал = програм ажиллаагүй.
    Цайны цаг цайвар, ажлын цагийн эхлэл/төгсгөл босоо зураас, сэжигтэй үе дээрээ улбар шар."""
    a, b = rng
    span = max(1, b - a)
    X = lambda sec: x + (min(b, max(a, sec)) - a) / span * w  # noqa: E731
    cv.create_rectangle(x, y, x + w, y + h, fill=GAP, outline="", tags=tags)
    s = settings or {}
    ls, le = dd.hm_sec(s.get("lunch_start")), dd.hm_sec(s.get("lunch_end"))
    if ls and le:
        cv.create_rectangle(X(ls), y, X(le), y + h, fill=LUNCH, outline="", tags=tags)
    for st, p0, p1 in periods or []:
        if st not in ("working", "away"):
            continue
        x0, x1 = X(dd.to_sec(p0)), X(dd.to_sec(p1))
        if x1 > x0:
            cv.create_rectangle(x0, y, max(x1, x0 + 1), y + h, fill=GREEN if st == "working" else RED, outline="",
                                tags=tags)
    for _kind, f0, f1 in flags or []:
        x0, x1 = X(dd.to_sec(f0)), X(dd.to_sec(f1))
        if x1 > x0:
            cv.create_rectangle(x0, y - 3, max(x1, x0 + 2), y + 1, fill=ORANGE, outline="", tags=tags)
    for key in ("work_start", "work_end"):
        sec = dd.hm_sec(s.get(key))
        if sec is not None and a <= sec <= b:
            cv.create_line(X(sec), y - 2, X(sec), y + h + 2, fill=FG, width=1, tags=tags)


class Dashboard(tk.Toplevel):
    def __init__(self, root: tk.Tk, api: ShareClient, profile: dict, bg: Bg):
        super().__init__(root)
        self.api, self.profile, self.bg = api, profile, bg
        self.is_admin = profile.get("role") == "admin"
        self.day = date.today()
        self.data: dict | None = None
        self._raw: dict | None = None    # нэгтгээгүй өгөгдөл — live шинэчлэлтэд дахин ашиглана
        self._live_sig: str | None = None
        self._job = None
        self._live_job = None
        self._loading = False
        self._live_loading = False

        self.title(f"Самбар — {APP_TITLE}")
        self.configure(bg=BG)
        self.geometry("1140x720")
        self.minsize(900, 500)

        style = ttk.Style(self)
        style.configure("Treeview", rowheight=26, font=(FONT, 11))
        style.configure("Treeview.Heading", font=(FONT, 11))

        # ---- толгой
        head = tk.Frame(self, bg=BG, padx=14, pady=10)
        head.pack(fill="x")
        button(head, "◀", lambda: self.shift(-1), width=2).pack(side="left")
        self.day_lbl = tk.Label(head, text="", font=(FONT, 13, "bold"), fg=FG, bg=BG, width=17)
        self.day_lbl.pack(side="left", padx=6)
        button(head, "▶", lambda: self.shift(1), width=2).pack(side="left")
        button(head, "Өнөөдөр", self.go_today).pack(side="left", padx=(8, 0))
        button(head, "Шинэчлэх", self.refresh).pack(side="left", padx=(8, 0))
        button(head, "CSV хадгалах", self.export_csv).pack(side="left", padx=(8, 0))
        self.info = tk.Label(head, text="", font=(FONT, 11), fg=MUTED, bg=BG)
        self.info.pack(side="right")

        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self._build_day_tab()
        self._build_week_tab()
        if self.is_admin:
            self._build_users_tab()
            self._build_departments_tab()
            self._build_settings_tab()

        self.protocol("WM_DELETE_WINDOW", self.close)
        self.refresh()
        self._live_job = self.after(LIVE_MS, self._live_tick)

    # ================================================================== өгөгдөл
    def shift(self, n: int) -> None:
        self.day += timedelta(days=n)
        self.refresh()

    def go_today(self) -> None:
        self.day = date.today()
        self.refresh()

    def refresh(self) -> None:
        if self._loading:
            return
        self._loading = True
        names = ["Ням", "Даваа", "Мягмар", "Лхагва", "Пүрэв", "Баасан", "Бямба"]
        self.day_lbl.config(text=f"{self.day.isoformat()} {names[(self.day.weekday() + 1) % 7]}")
        self.info.config(text="Татаж байна…", fg=MUTED)
        day = self.day.isoformat()
        self.bg.run(lambda: dd.fetch_raw(self.api, day), self._loaded, self._failed)

    def _loaded(self, raw: dict) -> None:
        self._loading = False
        if not self.winfo_exists():
            return
        if raw["day"] != self.day.isoformat():  # хооронд нь өдөр солигдсон
            return self.refresh()
        self._raw = raw
        self._live_sig = json.dumps(raw["live"], sort_keys=True, default=str)
        self.data = dd.build_raw(raw)
        self.info.config(text="Шинэчилсэн " + datetime.now().strftime("%H:%M:%S"), fg=MUTED)
        self._render_day()
        self._render_week()
        if self.is_admin:
            self._render_users()
            self._render_departments()
        self._schedule()

    def _failed(self, exc: Exception) -> None:
        self._loading = False
        if self.winfo_exists():
            self.info.config(text="Алдаа: " + friendly(exc)[:80], fg=RED)
            self._schedule()

    def _schedule(self) -> None:
        if self._job:
            self.after_cancel(self._job)
        self._job = self.after(REFRESH_MS, self._auto)

    def _auto(self) -> None:
        self._job = None
        if self.state() != "iconic":
            self.refresh()
        else:
            self._schedule()

    def _live_tick(self) -> None:
        """2 сек тутам зөвхөн одоогийн төлөв, аппыг уншина (хүн бүрт нэг жижиг файл)."""
        self._live_job = self.after(LIVE_MS, self._live_tick)
        if (self._loading or self._live_loading or self._raw is None or self.state() == "iconic"
                or self._raw["day"] != date.today().isoformat()):
            return
        self._live_loading = True
        self.bg.run(lambda: dd.fetch_live(self.api), self._live_loaded,
                    lambda _: setattr(self, "_live_loading", False))

    def _live_loaded(self, live: list) -> None:
        self._live_loading = False
        if not self.winfo_exists() or self._raw is None or self._loading:
            return
        sig = json.dumps(live, sort_keys=True, default=str)
        if sig == self._live_sig:
            return  # өөрчлөлтгүй — дахин зурахгүй
        self._live_sig = sig
        self._raw["live"] = live
        self.data = dd.build_raw(self._raw)
        self._render_day()

    def close(self) -> None:
        for job in (self._job, self._live_job):
            if job:
                self.after_cancel(job)
        self.destroy()

    # ================================================================== Өдөр
    def _build_day_tab(self) -> None:
        tab = tk.Frame(self.nb, bg=BG)
        self.nb.add(tab, text="Ажилтнууд")
        self.kpi = tk.Frame(tab, bg=BG, pady=8)
        self.kpi.pack(fill="x", padx=4)

        wrap = tk.Frame(tab, bg=BG, highlightthickness=1, highlightbackground=BORDER)
        wrap.pack(fill="both", expand=True, padx=4, pady=(0, 4))
        total_w = sum(w for _, w in COLS) + 20
        self.day_head = tk.Canvas(wrap, height=30, bg=SURFACE, highlightthickness=0)
        self.day_body = tk.Canvas(wrap, bg=BG, highlightthickness=0)
        ys = ttk.Scrollbar(wrap, orient="vertical", command=self.day_body.yview)
        xs = ttk.Scrollbar(wrap, orient="horizontal", command=self._xview)
        self.day_body.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        self.day_head.configure(scrollregion=(0, 0, total_w, 30))
        self.day_head.grid(row=0, column=0, sticky="ew")
        self.day_body.grid(row=1, column=0, sticky="nsew")
        ys.grid(row=1, column=1, sticky="ns")
        xs.grid(row=2, column=0, sticky="ew")
        wrap.rowconfigure(1, weight=1)
        wrap.columnconfigure(0, weight=1)
        x = 12
        for title, w in COLS:
            self.day_head.create_text(x, 15, text=title, anchor="w", font=(FONT, 11), fill=MUTED)
            x += w
        bind_wheel(self.day_body, self.day_body)
        legend = tk.Frame(tab, bg=BG)
        legend.pack(fill="x", padx=6, pady=(0, 4))
        for color, text in ((GREEN, "Ажилласан"), (RED, "Гарсан"), (GAP, "Програм ажиллаагүй"), (LUNCH, "Цайны цаг"),
                            (ORANGE, "Сэжигтэй (зураг / таглагдсан камер)")):
            tk.Label(legend, text="■", fg=color, bg=BG, font=(FONT, 12)).pack(side="left")
            tk.Label(legend, text=text + "   ", fg=MUTED, bg=BG, font=(FONT, 10)).pack(side="left")
        tk.Label(legend, text="Мөр дээр дарж дэлгэрэнгүйг харна", fg=MUTED, bg=BG, font=(FONT, 10)).pack(side="right")

    def _xview(self, *args) -> None:
        self.day_body.xview(*args)
        self.day_head.xview(*args)

    def _kpi(self, title: str, value: str, color: str = FG) -> None:
        card = tk.Frame(self.kpi, bg=BG, highlightthickness=1, highlightbackground=BORDER, padx=12, pady=6)
        card.pack(side="left", padx=(0, 8))
        tk.Label(card, text=title, font=(FONT, 10), fg=MUTED, bg=BG).pack(anchor="w")
        tk.Label(card, text=value, font=(FONT, 18, "bold"), fg=color, bg=BG).pack(anchor="w")

    def _render_day(self) -> None:
        data = self.data
        sset = data["settings"]
        people = [p for p in data["people"] if data["is_today"] or p["has_day"] or p["metrics"]["absent"]]
        for w in self.kpi.winfo_children():
            w.destroy()
        total = sum(p["day"]["work_seconds"] for p in people)
        worked = [p for p in people if p["day"]["work_seconds"]]
        self._kpi("Хүн", str(len(people)))
        if data["is_today"]:
            for st, title in (("working", "Ажиллаж байна"), ("away", "Гарсан"), ("offline", "Офлайн")):
                self._kpi(title, str(sum(1 for p in people if p["status"] == st)), STATUS_COLOR[st])
        self._kpi("Нийт ажилласан", fmt_dur(total))
        self._kpi("Дундаж / хүн", fmt_dur(total / len(worked)) if worked else "—")
        late = sum(1 for p in people if p["metrics"]["late"])
        absent = sum(1 for p in people if p["metrics"]["absent"])
        if late:
            self._kpi("Хоцорсон", str(late), ORANGE)
        if absent:
            self._kpi("Ирээгүй", str(absent), RED)
        sus = sum(1 for p in people if p["flag_sec"]["static"] or p["flag_sec"]["blocked"])
        if sus:
            self._kpi("Сэжигтэй", str(sus), ORANGE)

        cv = self.day_body
        cv.delete("all")
        total_w = sum(w for _, w in COLS) + 20
        if not people:
            cv.create_text(16, 30, anchor="w", font=(FONT, 12), fill=MUTED,
                           text="Энэ өдөр мэдээлэл алга." if not data["is_today"] else
                           "Одоогоор хэн ч бүртгэгдээгүй. Ажилтнуудын компьютерт програмыг суулгаж ажиллуулна уу.")
            cv.configure(scrollregion=(0, 0, total_w, 60))
            return
        rng = dd.day_range((p["day"]["periods"] for p in people), sset)
        for i, p in enumerate(people):
            y = i * ROW_H
            tag = f"row{i}"
            m, d, st = p["metrics"], p["day"], p["status"]
            cv.create_rectangle(0, y, total_w, y + ROW_H, fill=BG, outline="", tags=(tag, "bgrow"))
            cv.create_line(0, y + ROW_H - 1, total_w, y + ROW_H - 1, fill=GAP)
            x = 12
            warn = p["camera_state"] in ("static", "blocked") or p["flag_sec"]["static"] or p["flag_sec"]["blocked"]
            cv.create_text(x, y + 15, anchor="w", text=("⚠ " if warn else "") + p["name"][:24],
                           font=(FONT, 12, "bold"), fill=ORANGE if warn else FG, tags=tag)
            sub = " · ".join(t for t in (p["department"], (p["live"] or {}).get("hostname", "")) if t)
            cv.create_text(x, y + 31, anchor="w", text=sub, font=(FONT, 10), fill=MUTED, tags=tag)
            x += COLS[0][1]
            if m["absent"]:
                cv.create_text(x, y + ROW_H / 2, anchor="w", text="Ирээгүй", font=(FONT, 11, "bold"), fill=RED, tags=tag)
            else:
                cv.create_oval(x, y + 17, x + 9, y + 26, fill=STATUS_COLOR[st], outline="", tags=tag)
                cv.create_text(x + 15, y + 15, anchor="w", text=dd.STATUS_TEXT[st], font=(FONT, 11, "bold"),
                               fill=STATUS_COLOR[st], tags=tag)
                lv = p["live"] or {}
                if p["camera_state"] in ("static", "blocked"):
                    since = "⚠ " + dd.CAMERA_TEXT[p["camera_state"]]
                elif st != "offline":
                    since = ("⏸ зогсоосон · " if lv.get("paused") else "") + dd.ago_text(lv.get("status_since"))
                else:
                    since = (dd.ago_text(lv.get("last_seen_at")) + " өмнө") if lv.get("last_seen_at") else ""
                cv.create_text(x + 15, y + 31, anchor="w", text=since, font=(FONT, 10),
                               fill=ORANGE if since.startswith("⚠") else MUTED, tags=tag)
            x += COLS[1][1]
            app = ((p["live"] or {}).get("current_app") or "—") if st != "offline" else "—"
            cv.create_text(x, y + ROW_H / 2, anchor="w", text=app[:20], font=(FONT, 11), fill=FG, tags=tag)
            x += COLS[2][1]
            cv.create_text(x, y + 15, anchor="w", text=fmt_dur(d["work_seconds"]), font=(FONT, 12, "bold"),
                           fill=FG, tags=tag)
            if m["overtime"] >= 300:
                cv.create_text(x, y + 31, anchor="w", text=f"+{fmt_dur(m['overtime'])} илүү", font=(FONT, 9),
                               fill=GREEN, tags=tag)
            x += COLS[3][1]
            cv.create_text(x, y + 15, anchor="w", text=fmt_dur(m["away_net"]), font=(FONT, 11), fill=FG, tags=tag)
            if m["closed"] >= 300:
                cv.create_text(x, y + 31, anchor="w", text=f"хаалттай {fmt_dur(m['closed'])}", font=(FONT, 9),
                               fill=ORANGE, tags=tag)
            x += COLS[4][1]
            inout = f"{(d['first_seen'] or '—')[:5]} – {(d['last_seen'] or '—')[:5]}"
            cv.create_text(x, y + 15, anchor="w", text=inout, font=(FONT, 11), fill=FG, tags=tag)
            if m["early"]:
                cv.create_text(x, y + 31, anchor="w", text=f"{fmt_dur(m['early'])} эрт явсан", font=(FONT, 9),
                               fill=ORANGE, tags=tag)
            x += COLS[5][1]
            cv.create_text(x, y + ROW_H / 2, anchor="w", text=fmt_dur(m["late"]) if m["late"] else "—",
                           font=(FONT, 11, "bold" if m["late"] else "normal"), fill=ORANGE if m["late"] else MUTED,
                           tags=tag)
            x += COLS[6][1]
            pct = p["prod"]["pct"]
            cv.create_text(x, y + ROW_H / 2, anchor="w", text=f"{pct}%" if pct is not None else "—",
                           font=(FONT, 11, "bold"), fill=(GREEN if pct >= 60 else ORANGE if pct >= 30 else RED)
                           if pct is not None else MUTED, tags=tag)
            x += COLS[7][1]
            draw_timeline(cv, x, y + 16, COLS[8][1] - 16, 12, d["periods"], rng, sset, d["flags"], tags=(tag,))
            cv.tag_bind(tag, "<Button-1>", lambda _e, person=p: DetailWindow(self, person, self.data))
            cv.tag_bind(tag, "<Enter>", lambda _e, t=tag: (cv.itemconfigure(f"{t}&&bgrow", fill=SURFACE),
                                                           cv.configure(cursor="hand2")))
            cv.tag_bind(tag, "<Leave>", lambda _e, t=tag: (cv.itemconfigure(f"{t}&&bgrow", fill=BG),
                                                           cv.configure(cursor="")))
        cv.configure(scrollregion=(0, 0, total_w, len(people) * ROW_H))

    # ================================================================== 7 хоног
    def _build_week_tab(self) -> None:
        tab = tk.Frame(self.nb, bg=BG)
        self.nb.add(tab, text="7 хоног")
        wrap = tk.Frame(tab, bg=BG, highlightthickness=1, highlightbackground=BORDER)
        wrap.pack(fill="both", expand=True, padx=4, pady=8)
        self.week_cv = tk.Canvas(wrap, bg=BG, highlightthickness=0)
        ys = ttk.Scrollbar(wrap, orient="vertical", command=self.week_cv.yview)
        self.week_cv.configure(yscrollcommand=ys.set)
        self.week_cv.pack(side="left", fill="both", expand=True)
        ys.pack(side="right", fill="y")
        bind_wheel(self.week_cv, self.week_cv)

    def _render_week(self) -> None:
        cv = self.week_cv
        cv.delete("all")
        days = self.data["week_days"]
        name_w, cell_w, h = 220, 92, 36
        cv.create_rectangle(0, 0, name_w + cell_w * 8 + 20, h, fill=SURFACE, outline="")
        cv.create_text(12, h / 2, anchor="w", text="Ажилтан", font=(FONT, 11), fill=MUTED)
        for i, d in enumerate(days):
            wd = WEEKDAYS[date.fromisoformat(d).weekday()]
            cv.create_text(name_w + cell_w * i + cell_w / 2, h / 2, text=f"{wd} {d[5:].replace('-', '/')}",
                           font=(FONT, 11), fill=MUTED)
        cv.create_text(name_w + cell_w * 7 + cell_w / 2, h / 2, text="Нийт", font=(FONT, 11, "bold"), fill=MUTED)
        people = [p for p in self.data["people"] if p["week"] or p["week_absent"]]
        people.sort(key=lambda p: -sum(p["week"].values()))
        peak = max([8 * 3600] + [s for p in people for s in p["week"].values()])
        for r, p in enumerate(people):
            y = h + r * h
            cv.create_line(0, y + h, name_w + cell_w * 8 + 20, y + h, fill=GAP)
            cv.create_text(12, y + h / 2, anchor="w", text=p["name"][:28], font=(FONT, 12, "bold"), fill=FG)
            for i, d in enumerate(days):
                s = p["week"].get(d, 0)
                cx = name_w + cell_w * i
                if not s and d in p["week_absent"]:
                    cv.create_rectangle(cx + 6, y + 5, cx + cell_w - 6, y + h - 5, fill="#ffebe9", outline="")
                    cv.create_text(cx + cell_w / 2, y + h / 2, text="Ирээгүй", font=(FONT, 10, "bold"), fill=RED)
                elif s:
                    t = 0.15 + 0.7 * min(1.0, s / peak)
                    cv.create_rectangle(cx + 6, y + 5, cx + cell_w - 6, y + h - 5, fill=_mix(BG, GREEN, t),
                                        outline="")
                    cv.create_text(cx + cell_w / 2, y + h / 2, text=fmt_hm(s), font=(FONT, 11, "bold"),
                                   fill="white" if t > 0.5 else FG)
                else:
                    cv.create_text(cx + cell_w / 2, y + h / 2, text="·", font=(FONT, 12), fill=MUTED)
            cv.create_text(name_w + cell_w * 7 + cell_w / 2, y + h / 2, text=fmt_hm(sum(p["week"].values())),
                           font=(FONT, 12, "bold"), fill=FG)
        if not people:
            cv.create_text(16, h + 24, anchor="w", text="Мэдээлэл алга", font=(FONT, 12), fill=MUTED)
        cv.configure(scrollregion=(0, 0, name_w + cell_w * 8 + 20, h * (len(people) + 1) + 10))

    # ================================================================== Хэрэглэгчид (админ)
    def _build_users_tab(self) -> None:
        tab = tk.Frame(self.nb, bg=BG)
        self.users_tab = tab
        self.nb.add(tab, text="Хэрэглэгчид")
        cols = ("name", "host", "dept", "active", "ver")
        self.users_tv = ttk.Treeview(tab, columns=cols, show="headings", selectmode="browse")
        for c, title, w in zip(cols, ("Нэр", "Компьютер (хэрэглэгч@компьютер)", "Хэлтэс", "Төлөв", "Хувилбар"),
                               (230, 300, 190, 95, 90)):
            self.users_tv.heading(c, text=title)
            self.users_tv.column(c, width=w, anchor="w")
        self.users_tv.tag_configure("new", background=PENDING_BG)
        self.users_tv.tag_configure("inactive", foreground=MUTED)
        self.users_tv.pack(fill="both", expand=True, padx=4, pady=(8, 4))
        self.users_tv.bind("<<TreeviewSelect>>", self._user_selected)

        form = tk.Frame(tab, bg=BG, pady=8)
        form.pack(fill="x", padx=4)
        self.u_name = tk.StringVar()
        self.u_dept = tk.StringVar()
        self.u_active = tk.BooleanVar(value=True)
        tk.Label(form, text="Нэр", bg=BG, fg=MUTED, font=(FONT, 11)).grid(row=0, column=0, sticky="w")
        tk.Entry(form, textvariable=self.u_name, width=24, font=(FONT, 12)).grid(row=1, column=0, padx=(0, 10))
        tk.Label(form, text="Хэлтэс", bg=BG, fg=MUTED, font=(FONT, 11)).grid(row=0, column=2, sticky="w")
        self.u_dept_cb = ttk.Combobox(form, textvariable=self.u_dept, state="readonly", width=20)
        self.u_dept_cb.grid(row=1, column=2, padx=(0, 10))
        tk.Checkbutton(form, text="Идэвхтэй", variable=self.u_active, bg=BG, font=(FONT, 11)).grid(
            row=1, column=3, padx=(0, 10))
        button(form, "Хадгалах", self._save_user, primary=True).grid(row=1, column=4, padx=(0, 10))
        button(form, "🗑 Бүрмөсөн устгах", self._remove_user).grid(row=1, column=6, padx=(0, 10))
        button(form, "Устгасан хүмүүс…", self._show_removed).grid(row=1, column=7, padx=(0, 10))
        button(form, "Түлхүүр шинэчлэх", self._reset_device).grid(row=1, column=8)
        tk.Label(tab, text="Програм суулгасан хүн энд автоматаар гарна (хэлтэсгүй бол шараар). Хэлтсийг нь оноогоод "
                           "Хадгалах. Ажлаас гарсан хүнийг \"Бүрмөсөн устгах\" — түүний програм дахин бичихгүй.\n"
                           "\"Түлхүүр шинэчлэх\" — ажилтан програмаа дахин суулгасан / компьютер сольсон үед "
                           "(\"түлхүүр таарахгүй\" гэж гарвал).",
                 bg=BG, fg=MUTED, font=(FONT, 10)).pack(anchor="w", padx=6, pady=(0, 6))

    def _render_users(self) -> None:
        tv = self.users_tv
        sel = tv.selection()
        tv.delete(*tv.get_children())
        dept = {d["id"]: d["name"] for d in self.data["departments"]}
        profiles = sorted(self.data["profiles"], key=lambda p: (p["department_id"] is not None, not p["active"],
                                                                (p["full_name"] or p["email"]).lower()))
        for p in profiles:
            tags = ("inactive",) if not p["active"] else (("new",) if p["department_id"] is None else ())
            tv.insert("", "end", iid=p["user_id"], tags=tags, values=(
                p["full_name"] or "—", p["email"], dept.get(p["department_id"], "—"),
                "Идэвхтэй" if p["active"] else "Хаалттай", p.get("version") or "хуучин"))
        self.u_dept_cb.configure(values=["—"] + [d["name"] for d in self.data["departments"]])
        if sel and tv.exists(sel[0]):
            tv.selection_set(sel[0])

    def _selected_profile(self) -> dict | None:
        sel = self.users_tv.selection()
        if not sel:
            return None
        return next((p for p in self.data["profiles"] if p["user_id"] == sel[0]), None)

    def _user_selected(self, _=None) -> None:
        p = self._selected_profile()
        if not p:
            return
        dept = {d["id"]: d["name"] for d in self.data["departments"]}
        self.u_name.set(p["full_name"])
        self.u_dept.set(dept.get(p["department_id"], "—"))
        self.u_active.set(p["active"])

    def _save_user(self) -> None:
        p = self._selected_profile()
        if not p:
            return messagebox.showinfo(APP_TITLE, "Жагсаалтаас хүн сонгоно уу", parent=self)
        dept_id = next((d["id"] for d in self.data["departments"] if d["name"] == self.u_dept.get()), None)
        params = {"p_user": p["user_id"], "p_full_name": self.u_name.get(), "p_department_id": dept_id,
                  "p_active": bool(self.u_active.get())}
        self.bg.run(lambda: self.api.rpc("wt_admin_update_user", params),
                    lambda _: (self.info.config(text="Хадгаллаа", fg=GREEN), self.refresh()),
                    lambda e: messagebox.showerror(APP_TITLE, friendly(e), parent=self))

    def _remove_user(self) -> None:
        p = self._selected_profile()
        if not p:
            return messagebox.showinfo(APP_TITLE, "Жагсаалтаас хүн сонгоно уу", parent=self)
        name = p["full_name"] or p["email"]
        if not messagebox.askyesno(
                APP_TITLE, f"{name} ({p['email']})-г бүрмөсөн устгах уу?\n\n"
                           "• Хавтас дахь бүх цагийн түүх нь устна (буцааж авах боломжгүй)\n"
                           "• Самбар, жагсаалтаас алга болно\n"
                           "• Тэр компьютерийн програм цаашид өгөгдөл илгээхгүй\n\n"
                           "Андуурвал \"Устгасан хүмүүс…\"-ээс сэргээж болно (түүх нь буцаж ирэхгүй).",
                icon="warning", parent=self):
            return
        self.bg.run(lambda: self.api.rpc("wt_admin_remove_user", {"p_user": p["user_id"]}),
                    lambda _: (self.info.config(text=f"{name} устгагдлаа", fg=GREEN), self.refresh()),
                    lambda e: messagebox.showerror(APP_TITLE, friendly(e), parent=self))

    def _reset_device(self) -> None:
        p = self._selected_profile()
        if not p:
            return messagebox.showinfo(APP_TITLE, "Жагсаалтаас хүн сонгоно уу", parent=self)
        name = p["full_name"] or p["email"]
        if not messagebox.askyesno(APP_TITLE, f"{name}-ийн төхөөрөмжийн түлхүүрийг шинэчлэх үү?\n\n"
                                              "Тэр хүний програм дараагийн холболтоор (1 минутын дотор) шинэ түлхүүр "
                                              "автоматаар авна. Цагийн түүх хэвээр үлдэнэ.", parent=self):
            return
        self.bg.run(lambda: self.api.rpc("wt_admin_reset_device", {"p_user": p["user_id"]}),
                    lambda _: self.info.config(text=f"{name}: түлхүүр шинэчлэгдлээ", fg=GREEN),
                    lambda e: messagebox.showerror(APP_TITLE, friendly(e) if "not found" not in str(e) else
                                                   "Энэ хүн хуучин хувилбартай (түлхүүргүй) байна.", parent=self))

    def _show_removed(self) -> None:
        self.bg.run(lambda: self.api.company()["removed"], self._removed_loaded,
                    lambda e: messagebox.showerror(APP_TITLE, friendly(e), parent=self))

    def _removed_loaded(self, removed: dict) -> None:
        if not removed:
            return messagebox.showinfo(APP_TITLE, "Устгасан хүн алга.", parent=self)
        win = tk.Toplevel(self)
        win.title("Устгасан хүмүүс")
        win.configure(bg=BG, padx=14, pady=12)
        win.transient(self)
        tk.Label(win, text="Сэргээвэл тэр хүний програм (ажиллаж байвал) 1 минутын дотор дахин бүртгэгдэнэ.\n"
                           "Устгасан түүх буцаж ирэхгүй.", bg=BG, fg=MUTED, font=(FONT, 10), justify="left").pack(
            anchor="w")
        items = sorted(removed.items(), key=lambda kv: kv[1].get("removed_at", ""), reverse=True)
        lb = tk.Listbox(win, font=(FONT, 11), width=60, height=min(12, len(items)), activestyle="none")
        for uid, info in items:
            lb.insert("end", f"{info.get('full_name') or uid}   ·   {uid}   ·   {str(info.get('removed_at', ''))[:10]}")
        lb.pack(fill="both", expand=True, pady=8)

        def restore():
            sel = lb.curselection()
            if not sel:
                return
            uid = items[sel[0]][0]
            self.bg.run(lambda: self.api.rpc("wt_admin_restore_user", {"p_user": uid}),
                        lambda _: (win.destroy(), self.info.config(text="Сэргээлээ", fg=GREEN), self.refresh()),
                        lambda e: messagebox.showerror(APP_TITLE, friendly(e), parent=win))

        row = tk.Frame(win, bg=BG)
        row.pack(anchor="w")
        button(row, "Сэргээх", restore, primary=True).pack(side="left")
        button(row, "Хаах", win.destroy).pack(side="left", padx=8)

    # ================================================================== Хэлтэс (админ)
    def _build_departments_tab(self) -> None:
        tab = tk.Frame(self.nb, bg=BG, padx=8, pady=8)
        self.nb.add(tab, text="Хэлтэс")
        self.dept_lb = tk.Listbox(tab, font=(FONT, 12), height=14, width=42, activestyle="none", exportselection=False)
        self.dept_lb.grid(row=0, column=0, rowspan=9, sticky="nsw")
        self.dept_lb.bind("<<ListboxSelect>>", self._dept_selected)
        self.dept_name = tk.StringVar()
        self.dept_chat = tk.StringVar()
        lbl = lambda r, t: tk.Label(tab, text=t, bg=BG, fg=MUTED, font=(FONT, 11)).grid(  # noqa: E731
            row=r, column=1, sticky="w", padx=12)
        lbl(0, "Хэлтсийн нэр")
        tk.Entry(tab, textvariable=self.dept_name, width=30, font=(FONT, 12)).grid(row=1, column=1, padx=12, sticky="w")
        row = tk.Frame(tab, bg=BG)
        row.grid(row=2, column=1, sticky="w", padx=12, pady=8)
        button(row, "Шинээр нэмэх", lambda: self._save_dept(None), primary=True).pack(side="left")
        button(row, "Нэр солих", lambda: self._save_dept(self._dept_sel())).pack(side="left", padx=6)
        button(row, "Устгах", self._delete_dept).pack(side="left")
        tk.Label(tab, text="Хэлтэс устгахад тэнд байсан хүмүүс хэлтэсгүй болно.", bg=BG, fg=MUTED,
                 font=(FONT, 10)).grid(row=3, column=1, sticky="w", padx=12)
        lbl(5, "Энэ хэлтсийн мэдэгдэл очих Telegram чат (групп)-ын ID")
        self.dept_chat_cb = ttk.Combobox(tab, textvariable=self.dept_chat, width=34)
        self.dept_chat_cb.grid(row=6, column=1, padx=12, sticky="w")
        button(tab, "Чат хадгалах", self._save_dept_chat).grid(row=7, column=1, sticky="w", padx=12, pady=6)
        tk.Label(tab, text="Тохиргоо → Telegram хэсгээс \"Чат олох\" дарвал энд жагсаалт гарна.", bg=BG, fg=MUTED,
                 font=(FONT, 10)).grid(row=8, column=1, sticky="nw", padx=12)

    def _render_departments(self) -> None:
        sel = self.dept_lb.curselection()
        self.dept_lb.delete(0, "end")
        counts: dict = {}
        for p in self.data["profiles"]:
            counts[p["department_id"]] = counts.get(p["department_id"], 0) + 1
        for d in self.data["departments"]:
            chat = "  ✉" if d.get("telegram_chat_id") else ""
            self.dept_lb.insert("end", f"{d['name']}   ({counts.get(d['id'], 0)} хүн){chat}")
        if sel and sel[0] < len(self.data["departments"]):
            self.dept_lb.selection_set(sel[0])

    def _dept_sel(self) -> dict | None:
        sel = self.dept_lb.curselection()
        return self.data["departments"][sel[0]] if sel and self.data else None

    def _dept_selected(self, _=None) -> None:
        d = self._dept_sel()
        if d:
            self.dept_name.set(d["name"])
            self.dept_chat.set(d.get("telegram_chat_id") or "")

    def _save_dept(self, dept: dict | None) -> None:
        name = self.dept_name.get().strip()
        if not name:
            return
        params = {"p_id": dept["id"] if dept else None, "p_name": name}
        self.bg.run(lambda: self.api.rpc("wt_admin_save_department", params),
                    lambda _: (self.dept_name.set(""), self.refresh()),
                    lambda e: messagebox.showerror(APP_TITLE, friendly(e), parent=self))

    def _save_dept_chat(self) -> None:
        dept = self._dept_sel()
        if not dept:
            return messagebox.showinfo(APP_TITLE, "Жагсаалтаас хэлтэс сонгоно уу", parent=self)
        chat = self.dept_chat.get().split("  ·  ")[-1].strip()
        self.bg.run(lambda: self.api.rpc("wt_admin_set_department_chat", {"p_id": dept["id"], "p_chat": chat}),
                    lambda _: (self.info.config(text="Чат хадгаллаа", fg=GREEN), self.refresh()),
                    lambda e: messagebox.showerror(APP_TITLE, friendly(e), parent=self))

    def _delete_dept(self) -> None:
        dept = self._dept_sel()
        if not dept or not messagebox.askyesno(APP_TITLE, f"\"{dept['name']}\" хэлтсийг устгах уу?", parent=self):
            return
        self.bg.run(lambda: self.api.rpc("wt_admin_delete_department", {"p_id": dept["id"]}),
                    lambda _: self.refresh(),
                    lambda e: messagebox.showerror(APP_TITLE, friendly(e), parent=self))

    # ================================================================== Тохиргоо (админ)
    def _build_settings_tab(self) -> None:
        outer = tk.Frame(self.nb, bg=BG)
        self.nb.add(outer, text="Тохиргоо")
        cv = tk.Canvas(outer, bg=BG, highlightthickness=0)
        ys = ttk.Scrollbar(outer, orient="vertical", command=cv.yview)
        cv.configure(yscrollcommand=ys.set)
        ys.pack(side="right", fill="y")
        cv.pack(side="left", fill="both", expand=True)
        tab = tk.Frame(cv, bg=BG, padx=14, pady=10)
        cv.create_window(0, 0, window=tab, anchor="nw")
        tab.bind("<Configure>", lambda _e: cv.configure(scrollregion=cv.bbox("all")))
        bind_wheel(cv, cv)
        self.sv: dict[str, tk.Variable] = {}

        def section(col, title):
            f = tk.LabelFrame(tab, text=" " + title + " ", bg=BG, fg=FG, font=(FONT, 12, "bold"), padx=10, pady=6)
            f.grid(row=self._srow[col], column=col, sticky="nwe", padx=(0, 14), pady=(0, 10))
            self._srow[col] += 1
            return f

        def field(f, r, label, key, kind="entry", width=7, **kw):
            tk.Label(f, text=label, bg=BG, fg=FG, font=(FONT, 11)).grid(row=r, column=0, sticky="w", pady=3)
            if kind == "check":
                v = tk.BooleanVar()
                tk.Checkbutton(f, variable=v, bg=BG).grid(row=r, column=1, sticky="w", padx=8)
            elif kind == "spin":
                v = tk.IntVar()
                tk.Spinbox(f, textvariable=v, width=width, font=(FONT, 11), **kw).grid(row=r, column=1, sticky="w",
                                                                                     padx=8)
            else:
                v = tk.StringVar()
                tk.Entry(f, textvariable=v, width=width, font=(FONT, 11), **kw).grid(row=r, column=1, sticky="w",
                                                                                   padx=8)
            self.sv[key] = v

        self._srow = [0, 0]
        f = section(0, "Ажлын хуваарь")
        field(f, 0, "Ажил эхлэх (ЦЦ:ММ)", "work_start")
        field(f, 1, "Ажил дуусах", "work_end")
        field(f, 2, "Цайны цаг эхлэх", "lunch_start")
        field(f, 3, "Цайны цаг дуусах", "lunch_end")
        field(f, 4, "Хоцролтын хүлцэл (мин)", "late_grace_min", "spin", from_=0, to=120)
        tk.Label(f, text="Ажлын өдрүүд", bg=BG, fg=FG, font=(FONT, 11)).grid(row=5, column=0, sticky="w", pady=3)
        wd = tk.Frame(f, bg=BG)
        wd.grid(row=5, column=1, sticky="w", padx=4)
        self.s_days = []
        for i, name in enumerate(WEEKDAYS):
            v = tk.BooleanVar()
            tk.Checkbutton(wd, text=name, variable=v, bg=BG, font=(FONT, 10)).pack(side="left")
            self.s_days.append(v)

        f = section(0, "Илрүүлэлт")
        field(f, 0, "Царай харагдахгүй болоод \"Гарсан\" гэх (сек)", "away_grace_sec", "spin", from_=5, to=900,
              increment=5)
        field(f, 1, "Гар/хулгана хөдөлж байвал ажиллаж байна гэж үзэх", "input_counts_as_presence", "check")
        field(f, 2, "Зураг (хөдөлгөөнгүй царай) илрүүлэх", "liveness_enabled", "check")
        field(f, 3, "  … хэдэн секунд огт хөдлөхгүй бол сэжигтэй", "liveness_static_sec", "spin", from_=30,
              to=900, increment=10)
        field(f, 4, "Тайланд жагсаах богино гарсан хугацаа (мин)", "min_away_period_min", "spin", from_=1, to=60)
        field(f, 5, "Өдрийн тайлан илгээх цаг (имэйл/Telegram)", "report_time")

        f = section(0, "Апп ангилал (мөр бүрт нэг түлхүүр үг)")
        tk.Label(f, text="Бүтээмжтэй", bg=BG, fg=GREEN, font=(FONT, 11, "bold")).grid(row=0, column=0, sticky="w")
        tk.Label(f, text="Бүтээмжгүй", bg=BG, fg=RED, font=(FONT, 11, "bold")).grid(row=0, column=1, sticky="w",
                                                                                   padx=10)
        self.s_prod = tk.Text(f, width=24, height=7, font=(FONT, 11))
        self.s_unprod = tk.Text(f, width=24, height=7, font=(FONT, 11))
        self.s_prod.grid(row=1, column=0, sticky="w")
        self.s_unprod.grid(row=1, column=1, sticky="w", padx=10)
        tk.Label(f, text="Жишээ: Excel, Word, 1C, Visual Studio Code  /  YouTube, Facebook, Steam.\n"
                         "Аппын нэрэнд агуулагдвал тохирно (том жижиг үсэг хамаагүй). Хөтчийн доторх сайтыг ялгахгүй.",
                 bg=BG, fg=MUTED, font=(FONT, 10), justify="left").grid(row=2, column=0, columnspan=2, sticky="w")

        f = section(1, "Telegram мэдэгдэл")
        field(f, 0, "Мэдэгдэл асаах", "alerts_enabled", "check")
        tk.Label(f, text="Bot token (@BotFather)", bg=BG, fg=FG, font=(FONT, 11)).grid(row=1, column=0, sticky="w")
        self.s_token = tk.StringVar()
        tk.Entry(f, textvariable=self.s_token, width=34, font=(FONT, 11), show="•").grid(row=1, column=1, sticky="w",
                                                                                      padx=8)
        self.s_token_state = tk.Label(f, text="", bg=BG, fg=MUTED, font=(FONT, 10))
        self.s_token_state.grid(row=2, column=1, sticky="w", padx=8)
        tk.Label(f, text="Админы чат ID (бүх мэдэгдэл)", bg=BG, fg=FG, font=(FONT, 11)).grid(row=3, column=0,
                                                                                             sticky="w")
        self.s_admin_chat = tk.StringVar()
        self.s_chat_cb = ttk.Combobox(f, textvariable=self.s_admin_chat, width=32)
        self.s_chat_cb.grid(row=3, column=1, sticky="w", padx=8)
        row = tk.Frame(f, bg=BG)
        row.grid(row=4, column=1, sticky="w", padx=8, pady=4)
        button(row, "Чат олох", self._find_chats).pack(side="left")
        button(row, "Туршиж илгээх", self._test_telegram).pack(side="left", padx=6)
        field(f, 5, "Ирээгүй гэж мэдэгдэх (ажил эхэлснээс хойш, мин)", "alert_late_min", "spin", from_=5, to=240)
        field(f, 6, "Удаан гарсан гэж мэдэгдэх (мин)", "alert_away_min", "spin", from_=5, to=480)
        field(f, 7, "Програм хаалттай гэж мэдэгдэх (мин)", "alert_offline_min", "spin", from_=5, to=480)
        tk.Label(f, text="1) @BotFather → /newbot → токен.  2) Ботыг групп(үүд)-т нэмнэ.\n"
                         "3) \"Чат олох\" → админы чатыг сонгоно. Хэлтэс бүрийн чатыг Хэлтэс табаас.\n"
                         "Шалгалт 5 минут тутам энэ (админы) компьютер дээрх програм хийнэ.\n"
                         "Токен зөвхөн энэ компьютерт хадгалагдана (ажилтнууд харахгүй).",
                 bg=BG, fg=MUTED, font=(FONT, 10), justify="left").grid(row=8, column=0, columnspan=2, sticky="w",
                                                                        pady=(6, 0))

        bar = tk.Frame(tab, bg=BG)
        bar.grid(row=10, column=0, columnspan=2, sticky="w", pady=(4, 0))
        button(bar, "Бүх тохиргоог хадгалах", self._save_settings, primary=True).pack(side="left")
        tk.Label(bar, text="  Ажилтнуудын компьютерт 10 минутын дотор хүчинтэй болно.", bg=BG, fg=MUTED,
                 font=(FONT, 10)).pack(side="left")
        self._settings: dict = {}
        self.bg.run(lambda: (self.api.select("wt_company", "settings", id="eq.1"),
                             self.api.rpc("wt_admin_telegram_configured")), self._fill_settings)

    def _fill_settings(self, result) -> None:
        rows, configured = result
        raw = (rows[0]["settings"] if rows else None) or {}
        self._settings = raw
        s = dd.settings_with_defaults(raw)
        defaults = {"away_grace_sec": 20, "input_counts_as_presence": True, "liveness_enabled": True,
                    "liveness_static_sec": 120, "min_away_period_min": 1, "report_time": "18:00",
                    "alerts_enabled": False, "alert_late_min": 30, "alert_away_min": 40, "alert_offline_min": 15}
        for key, var in self.sv.items():
            val = s.get(key, defaults.get(key))
            try:
                var.set(val if val is not None else "")
            except tk.TclError:
                pass
        workdays = set(s.get("workdays") or [])
        for i, v in enumerate(self.s_days):
            v.set((i + 1) in workdays)
        for widget, key in ((self.s_prod, "app_productive"), (self.s_unprod, "app_unproductive")):
            widget.delete("1.0", "end")
            widget.insert("1.0", "\n".join(s.get(key) or []))
        self.s_admin_chat.set(raw.get("telegram_admin_chat", ""))
        self.s_token_state.config(text="✓ Токен хадгалагдсан (солих бол шинээр оруулна)" if configured
                                  else "Токен оруулаагүй", fg=GREEN if configured else MUTED)

    def _collect_settings(self) -> dict:
        new = dict(self._settings)
        for key in ("work_start", "work_end", "lunch_start", "lunch_end", "report_time"):
            v = self.sv[key].get().strip()
            if v:
                datetime.strptime(v, "%H:%M")
            new[key] = v
        for key in ("late_grace_min", "away_grace_sec", "liveness_static_sec", "min_away_period_min",
                    "alert_late_min", "alert_away_min", "alert_offline_min"):
            new[key] = int(self.sv[key].get())
        for key in ("input_counts_as_presence", "liveness_enabled", "alerts_enabled"):
            new[key] = bool(self.sv[key].get())
        new["workdays"] = [i + 1 for i, v in enumerate(self.s_days) if v.get()]
        for widget, key in ((self.s_prod, "app_productive"), (self.s_unprod, "app_unproductive")):
            new[key] = [ln.strip() for ln in widget.get("1.0", "end").splitlines() if ln.strip()]
        new["telegram_admin_chat"] = self.s_admin_chat.get().split("  ·  ")[-1].strip()
        new.setdefault("timezone", "Asia/Ulaanbaatar")
        return new

    def _save_settings(self) -> None:
        try:
            new = self._collect_settings()
        except (ValueError, tk.TclError):
            return messagebox.showerror(APP_TITLE, "Утга буруу байна (цаг: ЦЦ:ММ, тоо бүхэл)", parent=self)
        token = self.s_token.get().strip()

        def work():
            self.api.rpc("wt_admin_save_settings", {"p_settings": new})
            if token:
                self.api.rpc("wt_admin_set_telegram", {"p_token": token})

        def done(_):
            self._settings = new
            self.s_token.set("")
            if token:
                self.s_token_state.config(text="✓ Токен хадгалагдсан", fg=GREEN)
            messagebox.showinfo(APP_TITLE, "Хадгаллаа", parent=self)
            self.refresh()

        self.bg.run(work, done, lambda e: messagebox.showerror(APP_TITLE, friendly(e), parent=self))

    def _find_chats(self) -> None:
        from .notify import tg_find_chats

        token = self.s_token.get().strip()
        if not token:
            return messagebox.showinfo(APP_TITLE, "Bot token-оо оруулаад дарна уу (хадгалсан токеныг програм "
                                                  "буцааж уншдаггүй).", parent=self)

        def done(chats):
            values = [f"{name}  ·  {cid}" for name, cid in chats]
            self.s_chat_cb.configure(values=values)
            self.dept_chat_cb.configure(values=values)
            messagebox.showinfo(APP_TITLE, (f"{len(chats)} чат олдлоо. Жагсаалтаас сонгоно уу." if chats else
                                            "Чат олдсонгүй. Ботыг группт нэмээд, группт ямар нэг мессеж бичээд "
                                            "дахин оролдоно уу."), parent=self)

        self.bg.run(lambda: tg_find_chats(token), done,
                    lambda e: messagebox.showerror(APP_TITLE, friendly(e), parent=self))

    def _test_telegram(self) -> None:
        from .notify import tg_send

        token, chat = self.s_token.get().strip(), self.s_admin_chat.get().split("  ·  ")[-1].strip()
        if not token or not chat:
            return messagebox.showinfo(APP_TITLE, "Токен болон админы чатыг оруулна уу", parent=self)
        self.bg.run(lambda: tg_send(token, chat, "✅ Ажлын бүтээмж: туршилтын мэдэгдэл"),
                    lambda _: messagebox.showinfo(APP_TITLE, "Илгээлээ. Telegram-аа шалгана уу.", parent=self),
                    lambda e: messagebox.showerror(APP_TITLE, friendly(e), parent=self))

    # ================================================================== CSV
    def export_csv(self) -> None:
        if not self.data:
            return
        path = filedialog.asksaveasfilename(parent=self, defaultextension=".csv",
                                            initialfile=f"ajliin-buteemj-{self.data['day']}.csv",
                                            filetypes=[("CSV", "*.csv")])
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["Ажилтан", "Компьютер", "Хэлтэс", "Огноо", "Ажилласан (мин)", "Гарсан (мин)", "Ирсэн", "Явсан",
                        "Аппууд (мин)"])
            for p in self.data["people"]:
                d = p["day"]
                w.writerow([p["name"], p["email"], p["department"], self.data["day"],
                            round(d["work_seconds"] / 60), round(d["away_seconds"] / 60), d["first_seen"] or "",
                            d["last_seen"] or "", "; ".join(f"{a} {round(s / 60)}" for a, s in d["apps"])])
        self.info.config(text=f"Хадгаллаа: {path}", fg=GREEN)


class DetailWindow(tk.Toplevel):
    def __init__(self, parent: tk.Misc, p: dict, data: dict):
        super().__init__(parent)
        day, sset = data["day"], data["settings"]
        self.title(f"{p['name']} — {day}")
        self.configure(bg=BG, padx=18, pady=14)
        self.resizable(False, False)
        d, m, st = p["day"], p["metrics"], p["status"]
        lv = p["live"] or {}
        tk.Label(self, text=p["name"], font=(FONT, 17, "bold"), fg=FG, bg=BG).pack(anchor="w")
        cam = dd.CAMERA_TEXT.get(p.get("camera_state") or "", "—")
        meta = [dd.STATUS_TEXT[st], p["department"], lv.get("hostname", ""),
                f"камер: {cam}"]
        tk.Label(self, text=" · ".join(x for x in meta if x), font=(FONT, 11), fg=STATUS_COLOR[st], bg=BG).pack(
            anchor="w")

        def cards(items):
            row = tk.Frame(self, bg=BG, pady=6)
            row.pack(fill="x")
            for title, value, color in items:
                card = tk.Frame(row, bg=BG, highlightthickness=1, highlightbackground=BORDER, padx=12, pady=6)
                card.pack(side="left", padx=(0, 8))
                tk.Label(card, text=title, font=(FONT, 10), fg=MUTED, bg=BG).pack(anchor="w")
                tk.Label(card, text=value, font=(FONT, 16, "bold"), fg=color, bg=BG).pack(anchor="w")

        pct = p["prod"]["pct"]
        cards([("Ажилласан", fmt_dur(d["work_seconds"]), GREEN),
               ("Гарсан (цайны цаг хассан)", fmt_dur(m["away_net"]), RED),
               ("Ирсэн – Явсан", f"{(d['first_seen'] or '—')[:5]} – {(d['last_seen'] or '—')[:5]}", FG),
               ("Бүтээмж", f"{pct}%" if pct is not None else "—", GREEN if pct is not None else MUTED)])
        if m["workday"]:
            cards([("Хоцорсон", fmt_dur(m["late"]) if m["late"] else "—", ORANGE if m["late"] else MUTED),
                   ("Эрт явсан", fmt_dur(m["early"]) if m["early"] else "—", ORANGE if m["early"] else MUTED),
                   ("Илүү цаг", fmt_dur(m["overtime"]) if m["overtime"] else "—", GREEN if m["overtime"] else MUTED),
                   ("Програм хаалттай", fmt_dur(m["closed"]) if m["closed"] else "—",
                    ORANGE if m["closed"] else MUTED)])
        else:
            tk.Label(self, text="Амралтын өдөр — бүх ажилласан цаг илүү цагт тооцогдоно", font=(FONT, 11),
                     fg=MUTED, bg=BG).pack(anchor="w")

        W = 660
        tk.Label(self, text=f"Өдрийн явц  (ажлын цаг {sset.get('work_start')}–{sset.get('work_end')})",
                 font=(FONT, 12, "bold"), fg=FG, bg=BG).pack(anchor="w", pady=(8, 4))
        rng = dd.day_range([d["periods"]], sset)
        cv = tk.Canvas(self, width=W, height=46, bg=BG, highlightthickness=0)
        cv.pack(anchor="w")
        draw_timeline(cv, 0, 5, W, 22, d["periods"], rng, sset, d["flags"])
        step = 7200 if rng[1] - rng[0] > 10 * 3600 else 3600
        for h in range(rng[0], rng[1] + 1, step):
            x = (h - rng[0]) / max(1, rng[1] - rng[0]) * W
            cv.create_text(min(max(x, 18), W - 18), 38, text=f"{h // 3600:02d}:00", font=(FONT, 9), fill=MUTED)

        flags = [(k, a, b) for k, a, b in d["flags"] if dd.to_sec(b) - dd.to_sec(a) >= 30]
        if flags:
            tk.Label(self, text=f"⚠ Сэжигтэй үеүд ({len(flags)})", font=(FONT, 12, "bold"), fg=ORANGE, bg=BG).pack(
                anchor="w", pady=(8, 2))
            txt = "\n".join(f"{a[:5]} – {b[:5]}   {dd.FLAG_TEXT.get(k, k)}  ({fmt_dur(dd.to_sec(b) - dd.to_sec(a))})"
                            for k, a, b in flags[:8])
            tk.Label(self, text=txt, font=(FONT, 11), fg=FG, bg=BG, justify="left").pack(anchor="w")

        prod = p["prod"]
        head = "Апп тус бүр"
        if pct is not None:
            head += (f"   ·   бүтээмжтэй {fmt_dur(prod['productive'])}, бүтээмжгүй {fmt_dur(prod['unproductive'])}, "
                     f"бусад {fmt_dur(prod['neutral'])}")
        tk.Label(self, text=head, font=(FONT, 12, "bold"), fg=FG, bg=BG).pack(anchor="w", pady=(10, 4))
        apps = d["apps"][:12]
        total = sum(s for _, s in d["apps"]) or 1
        acv = tk.Canvas(self, width=W, height=max(26, 26 * len(apps)), bg=BG, highlightthickness=0)
        acv.pack(anchor="w")
        for i, (a, sec) in enumerate(apps):
            y = i * 26 + 13
            cat = dd.categorize(a, sset)
            acv.create_text(0, y, anchor="w", text=a[:30], font=(FONT, 11), fill=FG)
            acv.create_text(300, y, anchor="e", text=fmt_dur(sec), font=(FONT, 11), fill=FG)
            acv.create_rectangle(315, y - 5, 315 + max(2, 230 * sec / total), y + 5, fill=CAT_COLOR[cat], outline="")
            acv.create_text(W, y, anchor="e", text=f"{sec / total * 100:.0f}% {CAT_TEXT[cat]}", font=(FONT, 10),
                            fill=MUTED)
        if not apps:
            acv.create_text(0, 13, anchor="w", text="Мэдээлэл алга", font=(FONT, 11), fill=MUTED)

        aways = [(a, b) for st_, a, b in d["periods"] if st_ == "away" and dd.to_sec(b) - dd.to_sec(a) >= 60]
        tk.Label(self, text=f"Ажлаас гарсан үеүд ({len(aways)})", font=(FONT, 12, "bold"), fg=FG, bg=BG).pack(
            anchor="w", pady=(10, 4))
        txt = "\n".join(f"{a[:5]} – {b[:5]}   ({fmt_dur(dd.to_sec(b) - dd.to_sec(a))})" for a, b in aways[:12])
        if len(aways) > 12:
            txt += f"\n… бас {len(aways) - 12}"
        tk.Label(self, text=txt or "Байхгүй", font=(FONT, 11), fg=FG if aways else MUTED, bg=BG,
                 justify="left").pack(anchor="w")
        button(self, "Хаах", self.destroy).pack(anchor="e", pady=(10, 0))
