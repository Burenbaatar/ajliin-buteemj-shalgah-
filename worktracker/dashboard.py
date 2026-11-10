"""Програм доторх самбар (бүх эрхэд). Админд нэмэлт табууд: хэрэглэгч, хэлтэс, тохиргоо.

Хэн юуг харах нь серверийн RLS-ээр шийдэгдэнэ (ажилтан → өөрийгөө, менежер → хэлтсээ, админ → бүгдийг).
"""
from __future__ import annotations

import csv
import tkinter as tk
from datetime import date, datetime, timedelta
from tkinter import filedialog, messagebox, ttk

from . import APP_TITLE
from . import dashdata as dd
from .supa import ROLE_TEXT, SupaClient
from .uiutil import (BG, BORDER, FG, FONT, GAP, GREEN, MUTED, PENDING_BG, RED, STATUS_COLOR, SURFACE, Bg,
                     bind_wheel, button, fmt_dur, fmt_hm, friendly)

REFRESH_MS = 30000
ROW_H = 44
COLS = [("Ажилтан", 210), ("Одоо", 180), ("Одоогийн апп", 170), ("Ажилласан", 90), ("Гарсан", 80),
        ("Ирсэн – Явсан", 110), ("Өдрийн явц", 250)]
WEEKDAYS = ["Да", "Мя", "Лх", "Пү", "Ба", "Бя", "Ня"]
ROLE_BY_TEXT = {v: k for k, v in ROLE_TEXT.items()}


def _mix(hex_a: str, hex_b: str, t: float) -> str:
    a = [int(hex_a[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(hex_b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(a, b))


def draw_timeline(cv: tk.Canvas, x: int, y: int, w: int, h: int, periods, rng) -> None:
    a, b = rng
    span = max(1, b - a)
    cv.create_rectangle(x, y, x + w, y + h, fill=GAP, outline="")
    for st, s, e in periods or []:
        if st not in ("working", "away"):
            continue
        x0 = x + (max(a, dd.to_sec(s)) - a) / span * w
        x1 = x + (min(b, dd.to_sec(e)) - a) / span * w
        if x1 > x0:
            cv.create_rectangle(x0, y, max(x1, x0 + 1), y + h, fill=GREEN if st == "working" else RED, outline="")


class Dashboard(tk.Toplevel):
    def __init__(self, root: tk.Tk, supa: SupaClient, profile: dict, bg: Bg):
        super().__init__(root)
        self.supa, self.profile, self.bg = supa, profile, bg
        self.is_admin = profile.get("role") == "admin"
        self.day = date.today()
        self.data: dict | None = None
        self._job = None
        self._loading = False

        role = ROLE_TEXT.get(profile.get("role"), "")
        self.title(f"Самбар — {profile.get('full_name') or profile.get('email')} ({role})")
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
        self.bg.run(lambda: dd.load(self.supa, day), self._loaded, self._failed)

    def _loaded(self, data: dict) -> None:
        self._loading = False
        if not self.winfo_exists():
            return
        if data["day"] != self.day.isoformat():  # хооронд нь өдөр солигдсон
            return self.refresh()
        self.data = data
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

    def close(self) -> None:
        if self._job:
            self.after_cancel(self._job)
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
        for color, text in ((GREEN, "Ажилласан"), (RED, "Гарсан"), (GAP, "Компьютер унтраалттай / програм хаалттай")):
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
        people = [p for p in data["people"] if data["is_today"] or p["has_day"]]
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
        if self.is_admin and data["pending"]:
            self._kpi("Баталгаажуулах", str(len(data["pending"])), "#9a6700")

        cv = self.day_body
        cv.delete("all")
        total_w = sum(w for _, w in COLS) + 20
        if not people:
            cv.create_text(16, 30, anchor="w", font=(FONT, 12), fill=MUTED,
                           text="Энэ өдөр мэдээлэл алга." if not data["is_today"] else
                           "Харах хүн алга. Менежер бол админ таныг хэлтэст оноосон эсэхийг шалгана уу.")
            cv.configure(scrollregion=(0, 0, total_w, 60))
            return
        rng = dd.day_range(p["day"]["periods"] for p in people)
        for i, p in enumerate(people):
            y = i * ROW_H
            tag = f"row{i}"
            cv.create_rectangle(0, y, total_w, y + ROW_H, fill=BG, outline="", tags=(tag, "bgrow"))
            cv.create_line(0, y + ROW_H - 1, total_w, y + ROW_H - 1, fill=GAP)
            x = 12
            cv.create_text(x, y + 15, anchor="w", text=p["name"][:26], font=(FONT, 12, "bold"), fill=FG, tags=tag)
            sub = " · ".join(s for s in (p["department"], ROLE_TEXT.get(p["role"], "")) if s)
            cv.create_text(x, y + 31, anchor="w", text=sub, font=(FONT, 10), fill=MUTED, tags=tag)
            x += COLS[0][1]
            st = p["status"]
            cv.create_oval(x, y + 17, x + 9, y + 26, fill=STATUS_COLOR[st], outline="", tags=tag)
            cv.create_text(x + 15, y + 15, anchor="w", text=dd.STATUS_TEXT[st], font=(FONT, 11, "bold"),
                           fill=STATUS_COLOR[st], tags=tag)
            lv = p["live"] or {}
            since = dd.ago_text(lv.get("status_since")) if st != "offline" else (
                (dd.ago_text(lv.get("last_seen_at")) + " өмнө") if lv.get("last_seen_at") else "")
            cv.create_text(x + 15, y + 31, anchor="w", text=since, font=(FONT, 10), fill=MUTED, tags=tag)
            x += COLS[1][1]
            app = (lv.get("current_app") or "—") if st != "offline" else "—"
            cv.create_text(x, y + ROW_H / 2, anchor="w", text=app[:22], font=(FONT, 11), fill=FG, tags=tag)
            x += COLS[2][1]
            d = p["day"]
            cv.create_text(x, y + ROW_H / 2, anchor="w", text=fmt_dur(d["work_seconds"]), font=(FONT, 12, "bold"),
                           fill=FG, tags=tag)
            x += COLS[3][1]
            cv.create_text(x, y + ROW_H / 2, anchor="w", text=fmt_dur(d["away_seconds"]), font=(FONT, 11), fill=FG,
                           tags=tag)
            x += COLS[4][1]
            inout = f"{(d['first_seen'] or '—')[:5]} – {(d['last_seen'] or '—')[:5]}"
            cv.create_text(x, y + ROW_H / 2, anchor="w", text=inout, font=(FONT, 11), fill=FG, tags=tag)
            x += COLS[5][1]
            draw_timeline(cv, x, y + 16, COLS[6][1] - 16, 12, d["periods"], rng)
            cv.tag_bind(tag, "<Button-1>", lambda _e, person=p: DetailWindow(self, person, self.data["day"]))
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
        people = [p for p in self.data["people"] if p["week"]]
        people.sort(key=lambda p: -sum(p["week"].values()))
        peak = max([8 * 3600] + [s for p in people for s in p["week"].values()])
        for r, p in enumerate(people):
            y = h + r * h
            cv.create_line(0, y + h, name_w + cell_w * 8 + 20, y + h, fill=GAP)
            cv.create_text(12, y + h / 2, anchor="w", text=p["name"][:28], font=(FONT, 12, "bold"), fill=FG)
            for i, d in enumerate(days):
                s = p["week"].get(d, 0)
                cx = name_w + cell_w * i
                if s:
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
        cols = ("name", "email", "role", "dept", "active")
        self.users_tv = ttk.Treeview(tab, columns=cols, show="headings", selectmode="browse")
        for c, title, w in zip(cols, ("Нэр", "Имэйл", "Эрх", "Хэлтэс", "Төлөв"), (220, 260, 130, 180, 100)):
            self.users_tv.heading(c, text=title)
            self.users_tv.column(c, width=w, anchor="w")
        self.users_tv.tag_configure("pending", background=PENDING_BG)
        self.users_tv.tag_configure("inactive", foreground=MUTED)
        self.users_tv.pack(fill="both", expand=True, padx=4, pady=(8, 4))
        self.users_tv.bind("<<TreeviewSelect>>", self._user_selected)

        form = tk.Frame(tab, bg=BG, pady=8)
        form.pack(fill="x", padx=4)
        self.u_name = tk.StringVar()
        self.u_role = tk.StringVar()
        self.u_dept = tk.StringVar()
        self.u_active = tk.BooleanVar(value=True)
        tk.Label(form, text="Нэр", bg=BG, fg=MUTED, font=(FONT, 11)).grid(row=0, column=0, sticky="w")
        tk.Entry(form, textvariable=self.u_name, width=24, font=(FONT, 12)).grid(row=1, column=0, padx=(0, 10))
        tk.Label(form, text="Эрх", bg=BG, fg=MUTED, font=(FONT, 11)).grid(row=0, column=1, sticky="w")
        ttk.Combobox(form, textvariable=self.u_role, values=list(ROLE_TEXT.values()), state="readonly",
                     width=16).grid(row=1, column=1, padx=(0, 10))
        tk.Label(form, text="Хэлтэс", bg=BG, fg=MUTED, font=(FONT, 11)).grid(row=0, column=2, sticky="w")
        self.u_dept_cb = ttk.Combobox(form, textvariable=self.u_dept, state="readonly", width=20)
        self.u_dept_cb.grid(row=1, column=2, padx=(0, 10))
        tk.Checkbutton(form, text="Идэвхтэй", variable=self.u_active, bg=BG, font=(FONT, 11)).grid(
            row=1, column=3, padx=(0, 10))
        button(form, "Хадгалах", self._save_user, primary=True).grid(row=1, column=4, padx=(0, 10))
        button(form, "Цагийн түүхийг устгах", self._delete_user_data).grid(row=1, column=5)
        tk.Label(tab, text="Шинэ хүн програмаас бүртгүүлэхэд энд шараар гарна. Эрх, хэлтсийг нь оноогоод Хадгалах.",
                 bg=BG, fg=MUTED, font=(FONT, 10)).pack(anchor="w", padx=6, pady=(0, 6))

    def _render_users(self) -> None:
        tv = self.users_tv
        sel = tv.selection()
        tv.delete(*tv.get_children())
        dept = {d["id"]: d["name"] for d in self.data["departments"]}
        profiles = sorted(self.data["profiles"], key=lambda p: (p["role"] != "pending", not p["active"],
                                                                (p["full_name"] or p["email"]).lower()))
        for p in profiles:
            tags = ("pending",) if p["role"] == "pending" else (() if p["active"] else ("inactive",))
            tv.insert("", "end", iid=p["user_id"], tags=tags, values=(
                p["full_name"] or "—", p["email"], ROLE_TEXT.get(p["role"], p["role"]),
                dept.get(p["department_id"], "—"), "Идэвхтэй" if p["active"] else "Хаалттай"))
        self.u_dept_cb.configure(values=["—"] + [d["name"] for d in self.data["departments"]])
        n = len(self.data["pending"])
        self.nb.tab(self.users_tab, text=f"Хэрэглэгчид ({n} хүлээгдэж буй)" if n else "Хэрэглэгчид")
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
        self.u_role.set(ROLE_TEXT["employee"] if p["role"] == "pending" else ROLE_TEXT[p["role"]])
        self.u_dept.set(dept.get(p["department_id"], "—"))
        self.u_active.set(p["active"])

    def _save_user(self) -> None:
        p = self._selected_profile()
        if not p:
            return messagebox.showinfo(APP_TITLE, "Жагсаалтаас хүн сонгоно уу", parent=self)
        dept_id = next((d["id"] for d in self.data["departments"] if d["name"] == self.u_dept.get()), None)
        role = ROLE_BY_TEXT.get(self.u_role.get(), "employee")
        if role == "manager" and dept_id is None:
            if not messagebox.askyesno(APP_TITLE, "Хэлтэсгүй менежер зөвхөн өөрийгөө харна. Үргэлжлүүлэх үү?",
                                       parent=self):
                return
        params = {"p_user": p["user_id"], "p_full_name": self.u_name.get(), "p_role": role,
                  "p_department_id": dept_id, "p_active": bool(self.u_active.get())}
        self.bg.run(lambda: self.supa.rpc("wt_admin_update_user", params),
                    lambda _: (self.info.config(text="Хадгаллаа", fg=GREEN), self.refresh()),
                    lambda e: messagebox.showerror(APP_TITLE, friendly(e), parent=self))

    def _delete_user_data(self) -> None:
        p = self._selected_profile()
        if not p:
            return
        if not messagebox.askyesno(APP_TITLE, f"{p['full_name'] or p['email']}-ийн бүх цагийн түүхийг устгаж, "
                                              "бүртгэлийг хаах уу? Буцаах боломжгүй.", parent=self):
            return
        self.bg.run(lambda: self.supa.rpc("wt_admin_delete_user_data", {"p_user": p["user_id"]}),
                    lambda _: self.refresh(),
                    lambda e: messagebox.showerror(APP_TITLE, friendly(e), parent=self))

    # ================================================================== Хэлтэс (админ)
    def _build_departments_tab(self) -> None:
        tab = tk.Frame(self.nb, bg=BG, padx=8, pady=8)
        self.nb.add(tab, text="Хэлтэс")
        self.dept_lb = tk.Listbox(tab, font=(FONT, 12), height=14, width=40, activestyle="none")
        self.dept_lb.grid(row=0, column=0, rowspan=6, sticky="nsw")
        self.dept_lb.bind("<<ListboxSelect>>", lambda _: self.dept_name.set(self._dept_sel()["name"])
                          if self._dept_sel() else None)
        self.dept_name = tk.StringVar()
        tk.Label(tab, text="Хэлтсийн нэр", bg=BG, fg=MUTED, font=(FONT, 11)).grid(row=0, column=1, sticky="w",
                                                                                 padx=12)
        tk.Entry(tab, textvariable=self.dept_name, width=28, font=(FONT, 12)).grid(row=1, column=1, padx=12,
                                                                                  sticky="w")
        row = tk.Frame(tab, bg=BG)
        row.grid(row=2, column=1, sticky="w", padx=12, pady=8)
        button(row, "Шинээр нэмэх", lambda: self._save_dept(None), primary=True).pack(side="left")
        button(row, "Нэр солих", lambda: self._save_dept(self._dept_sel())).pack(side="left", padx=6)
        button(row, "Устгах", self._delete_dept).pack(side="left")
        tk.Label(tab, text="Хэлтэс устгахад тэнд байсан хүмүүс хэлтэсгүй болно.", bg=BG, fg=MUTED,
                 font=(FONT, 10)).grid(row=3, column=1, sticky="w", padx=12)

    def _render_departments(self) -> None:
        self.dept_lb.delete(0, "end")
        counts: dict = {}
        for p in self.data["profiles"]:
            counts[p["department_id"]] = counts.get(p["department_id"], 0) + 1
        for d in self.data["departments"]:
            self.dept_lb.insert("end", f"{d['name']}   ({counts.get(d['id'], 0)} хүн)")

    def _dept_sel(self) -> dict | None:
        sel = self.dept_lb.curselection()
        return self.data["departments"][sel[0]] if sel and self.data else None

    def _save_dept(self, dept: dict | None) -> None:
        name = self.dept_name.get().strip()
        if not name:
            return
        params = {"p_id": dept["id"] if dept else None, "p_name": name}
        self.bg.run(lambda: self.supa.rpc("wt_admin_save_department", params),
                    lambda _: (self.dept_name.set(""), self.refresh()),
                    lambda e: messagebox.showerror(APP_TITLE, friendly(e), parent=self))

    def _delete_dept(self) -> None:
        dept = self._dept_sel()
        if not dept or not messagebox.askyesno(APP_TITLE, f"\"{dept['name']}\" хэлтсийг устгах уу?", parent=self):
            return
        self.bg.run(lambda: self.supa.rpc("wt_admin_delete_department", {"p_id": dept["id"]}),
                    lambda _: self.refresh(),
                    lambda e: messagebox.showerror(APP_TITLE, friendly(e), parent=self))

    # ================================================================== Тохиргоо (админ)
    def _build_settings_tab(self) -> None:
        tab = tk.Frame(self.nb, bg=BG, padx=14, pady=14)
        self.nb.add(tab, text="Тохиргоо")
        self.s_grace = tk.IntVar(value=20)
        self.s_minaway = tk.IntVar(value=1)
        self.s_input = tk.BooleanVar(value=True)
        self.s_report = tk.StringVar(value="18:00")
        rows = [
            ("Царай харагдахгүй болоод хэдэн секундэд \"Гарсан\" гэж үзэх", tk.Spinbox(
                tab, from_=5, to=900, increment=5, textvariable=self.s_grace, width=6, font=(FONT, 12))),
            ("Тайланд жагсаах хамгийн богино гарсан хугацаа (минут)", tk.Spinbox(
                tab, from_=1, to=60, textvariable=self.s_minaway, width=6, font=(FONT, 12))),
            ("Өдрийн тайлан илгээх цаг (имэйл/Telegram тохируулсан бол)", tk.Entry(
                tab, textvariable=self.s_report, width=7, font=(FONT, 12))),
        ]
        for i, (label, widget) in enumerate(rows):
            tk.Label(tab, text=label, bg=BG, fg=FG, font=(FONT, 12)).grid(row=i, column=0, sticky="w", pady=6)
            widget.grid(row=i, column=1, sticky="w", padx=12)
        tk.Checkbutton(tab, text="Гар/хулгана хөдөлж байвал (царай танигдаагүй ч) ажиллаж байна гэж үзэх",
                       variable=self.s_input, bg=BG, font=(FONT, 12)).grid(row=3, column=0, columnspan=2,
                                                                            sticky="w", pady=6)
        button(tab, "Хадгалах", self._save_settings, primary=True).grid(row=4, column=0, sticky="w", pady=(12, 0))
        tk.Label(tab, text="Бүх компьютер дээр 10 минутын дотор хүчинтэй болно.", bg=BG, fg=MUTED,
                 font=(FONT, 10)).grid(row=5, column=0, sticky="w", pady=(6, 0))
        self.bg.run(lambda: self.supa.select("wt_company", "settings", id="eq.1"), self._fill_settings)

    def _fill_settings(self, rows) -> None:
        s = (rows[0]["settings"] if rows else None) or {}
        self._settings = s
        self.s_grace.set(int(s.get("away_grace_sec", 20)))
        self.s_minaway.set(int(s.get("min_away_period_min", 1)))
        self.s_input.set(bool(s.get("input_counts_as_presence", True)))
        self.s_report.set(str(s.get("report_time", "18:00")))

    def _save_settings(self) -> None:
        try:
            datetime.strptime(self.s_report.get().strip(), "%H:%M")
            new = dict(getattr(self, "_settings", {}), away_grace_sec=int(self.s_grace.get()),
                       min_away_period_min=int(self.s_minaway.get()),
                       input_counts_as_presence=bool(self.s_input.get()), report_time=self.s_report.get().strip())
        except (ValueError, tk.TclError):
            return messagebox.showerror(APP_TITLE, "Утгууд буруу байна (цаг: ЦЦ:ММ)", parent=self)
        self.bg.run(lambda: self.supa.rpc("wt_admin_save_settings", {"p_settings": new}),
                    lambda _: (setattr(self, "_settings", new), messagebox.showinfo(APP_TITLE, "Хадгаллаа",
                                                                                     parent=self)),
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
            w.writerow(["Ажилтан", "Имэйл", "Хэлтэс", "Огноо", "Ажилласан (мин)", "Гарсан (мин)", "Ирсэн", "Явсан",
                        "Аппууд (мин)"])
            for p in self.data["people"]:
                d = p["day"]
                w.writerow([p["name"], p["email"], p["department"], self.data["day"],
                            round(d["work_seconds"] / 60), round(d["away_seconds"] / 60), d["first_seen"] or "",
                            d["last_seen"] or "", "; ".join(f"{a} {round(s / 60)}" for a, s in d["apps"])])
        self.info.config(text=f"Хадгаллаа: {path}", fg=GREEN)


class DetailWindow(tk.Toplevel):
    def __init__(self, parent: tk.Misc, p: dict, day: str):
        super().__init__(parent)
        self.title(f"{p['name']} — {day}")
        self.configure(bg=BG, padx=18, pady=14)
        self.resizable(False, False)
        d = p["day"]
        st = p["status"]
        lv = p["live"] or {}
        tk.Label(self, text=p["name"], font=(FONT, 17, "bold"), fg=FG, bg=BG).pack(anchor="w")
        meta = [dd.STATUS_TEXT[st], p["department"], ROLE_TEXT.get(p["role"], ""), lv.get("hostname", ""),
                "камер: " + ("OK" if lv.get("camera_ok") else "олдсонгүй" if lv.get("camera_ok") is False else "—")]
        tk.Label(self, text=" · ".join(m for m in meta if m), font=(FONT, 11), fg=STATUS_COLOR[st], bg=BG).pack(
            anchor="w")

        kpi = tk.Frame(self, bg=BG, pady=10)
        kpi.pack(fill="x")
        for title, value, color in (("Ажилласан", fmt_dur(d["work_seconds"]), GREEN),
                                    ("Гарсан", fmt_dur(d["away_seconds"]), RED),
                                    ("Ирсэн – Явсан", f"{(d['first_seen'] or '—')[:5]} – {(d['last_seen'] or '—')[:5]}",
                                     FG)):
            card = tk.Frame(kpi, bg=BG, highlightthickness=1, highlightbackground=BORDER, padx=12, pady=6)
            card.pack(side="left", padx=(0, 8))
            tk.Label(card, text=title, font=(FONT, 10), fg=MUTED, bg=BG).pack(anchor="w")
            tk.Label(card, text=value, font=(FONT, 17, "bold"), fg=color, bg=BG).pack(anchor="w")

        W = 640
        tk.Label(self, text="Өдрийн явц", font=(FONT, 12, "bold"), fg=FG, bg=BG).pack(anchor="w", pady=(6, 4))
        rng = dd.day_range([d["periods"]])
        cv = tk.Canvas(self, width=W, height=44, bg=BG, highlightthickness=0)
        cv.pack(anchor="w")
        draw_timeline(cv, 0, 2, W, 22, d["periods"], rng)
        step = 7200 if rng[1] - rng[0] > 10 * 3600 else 3600
        for h in range(rng[0], rng[1] + 1, step):
            x = (h - rng[0]) / max(1, rng[1] - rng[0]) * W
            cv.create_text(min(max(x, 18), W - 18), 36, text=f"{h // 3600:02d}:00", font=(FONT, 9), fill=MUTED)

        tk.Label(self, text="Апп тус бүр", font=(FONT, 12, "bold"), fg=FG, bg=BG).pack(anchor="w", pady=(10, 4))
        apps = d["apps"][:12]
        total = sum(s for _, s in d["apps"]) or 1
        acv = tk.Canvas(self, width=W, height=max(26, 26 * len(apps)), bg=BG, highlightthickness=0)
        acv.pack(anchor="w")
        for i, (a, s) in enumerate(apps):
            y = i * 26 + 13
            acv.create_text(0, y, anchor="w", text=a[:30], font=(FONT, 11), fill=FG)
            acv.create_text(310, y, anchor="e", text=fmt_dur(s), font=(FONT, 11), fill=FG)
            acv.create_rectangle(325, y - 5, 325 + max(2, 260 * s / total), y + 5, fill="#2f81f7", outline="")
            acv.create_text(W, y, anchor="e", text=f"{s / total * 100:.0f}%", font=(FONT, 10), fill=MUTED)
        if not apps:
            acv.create_text(0, 13, anchor="w", text="Мэдээлэл алга", font=(FONT, 11), fill=MUTED)

        aways = [(s, e) for st_, s, e in d["periods"] if st_ == "away" and dd.to_sec(e) - dd.to_sec(s) >= 60]
        tk.Label(self, text=f"Ажлаас гарсан үеүд ({len(aways)})", font=(FONT, 12, "bold"), fg=FG, bg=BG).pack(
            anchor="w", pady=(10, 4))
        txt = "\n".join(f"{s[:5]} – {e[:5]}   ({fmt_dur(dd.to_sec(e) - dd.to_sec(s))})" for s, e in aways[:14])
        if len(aways) > 14:
            txt += f"\n… бас {len(aways) - 14}"
        tk.Label(self, text=txt or "Байхгүй", font=(FONT, 11), fg=FG if aways else MUTED, bg=BG,
                 justify="left").pack(anchor="w")
        button(self, "Хаах", self.destroy).pack(anchor="e", pady=(10, 0))
