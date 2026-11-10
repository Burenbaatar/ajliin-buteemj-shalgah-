"""Үндсэн цонх: нэвтрэх → "Ажиллаж байна" / "Ажлаас гарсан" төлөв, таймер, аппууд, самбар."""
from __future__ import annotations

import logging
import tkinter as tk
import webbrowser
from datetime import date
from tkinter import messagebox, simpledialog

from . import APP_TITLE
from .app import App
from .login import AuthScreen
from .notify import channels_enabled
from .report import build_report, save_report
from .supa import ROLE_TEXT, SupaClient
from .tracker import WORKING
from .uiutil import BG, FG, FONT, GREEN, MUTED, RED, Bg, button, clear, fmt_dur, friendly

log = logging.getLogger(__name__)
TOP_N = 6


def _hms(sec: float) -> str:
    sec = int(sec)
    return f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


class MainWindow:
    def __init__(self, root: tk.Tk, app: App, bg: Bg, on_logout):
        self.root, self.app, self.bg, self.on_logout = root, app, bg, on_logout
        self.dashboard = None
        self._job = None
        clear(root)
        root.title(APP_TITLE)
        root.configure(bg=BG)
        root.resizable(False, False)
        if app.cfg.get("always_on_top"):
            root.attributes("-topmost", True)
        root.protocol("WM_DELETE_WINDOW", self.on_close)

        # --- төлөвийн самбар
        self.banner = tk.Frame(root, bg=GREEN, padx=16, pady=14)
        self.banner.pack(fill="x")
        self.status_lbl = tk.Label(self.banner, text="", font=(FONT, 18, "bold"), fg="white", bg=GREEN)
        self.status_lbl.pack(anchor="w")
        self.timer_lbl = tk.Label(self.banner, text="", font=(FONT, 28, "bold"), fg="white", bg=GREEN)
        self.timer_lbl.pack(anchor="w")
        self.timer_caption = tk.Label(self.banner, text="Өнөөдөр ажилласан", font=(FONT, 11), fg="white", bg=GREEN)
        self.timer_caption.pack(anchor="w")

        # --- мэдээлэл
        body = tk.Frame(root, bg=BG, padx=16, pady=10)
        body.pack(fill="both", expand=True)
        self.rows: dict[str, tk.Label] = {}
        for i, (key, caption) in enumerate([("name", "Хэрэглэгч"), ("role", "Эрх"), ("away", "Ажлаас гарсан"),
                                            ("app", "Одоогийн апп"), ("cam", "Камер"), ("cloud", "Сервер")]):
            tk.Label(body, text=caption, font=(FONT, 11), fg=MUTED, bg=BG).grid(row=i, column=0, sticky="w", pady=2)
            lbl = tk.Label(body, text="", font=(FONT, 11), fg=FG, bg=BG, anchor="w", width=28)
            lbl.grid(row=i, column=1, sticky="w", padx=(10, 0), pady=2)
            self.rows[key] = lbl
        p = app.profile or {}
        self.rows["name"].config(text=p.get("full_name") or p.get("email") or app.cfg["employee_name"] or "-")
        self.rows["role"].config(text=ROLE_TEXT.get(p.get("role"), "-"))
        if p.get("department_id") and app.supa:
            self.bg.run(lambda: app.supa.select("wt_departments", "name", id=f"eq.{p['department_id']}"),
                        lambda rows: rows and self.rows["role"].config(
                            text=f"{ROLE_TEXT.get(p.get('role'), '')} · {rows[0]['name']}"))

        tk.Label(body, text="Өнөөдрийн аппууд", font=(FONT, 12, "bold"), fg=FG, bg=BG).grid(
            row=6, column=0, columnspan=2, sticky="w", pady=(12, 4))
        self.app_rows: list[tuple[tk.Label, tk.Label]] = []
        for i in range(TOP_N):
            a = tk.Label(body, text="", font=(FONT, 11), fg=FG, bg=BG, anchor="w")
            b = tk.Label(body, text="", font=(FONT, 11), fg=MUTED, bg=BG, anchor="e")
            a.grid(row=7 + i, column=0, sticky="w")
            b.grid(row=7 + i, column=1, sticky="e")
            self.app_rows.append((a, b))

        # --- товчнууд
        btns = tk.Frame(root, bg=BG, padx=16, pady=8)
        btns.pack(fill="x")
        if app.supa:
            title = "Самбар" if p.get("role") in ("manager", "admin") else "Миний түүх"
            button(btns, title, self.open_dashboard, primary=True).pack(side="left")
        button(btns, "Тайлан", self.open_report).pack(side="left", padx=(8, 0))
        if channels_enabled(app.cfg):
            self.send_btn = button(btns, "Илгээх", self.send_report)
            self.send_btn.pack(side="left", padx=(8, 0))
        if app.supa:
            button(btns, "Гарах", self.logout).pack(side="right")
        foot = tk.Frame(root, bg=BG, pady=6)
        foot.pack(fill="x")
        tk.Label(foot, text="Дэлгэц бичихгүй · Зураг хадгалахгүй", font=(FONT, 10), fg=MUTED, bg=BG).pack(
            side="left", padx=16)
        if app.supa:
            link = tk.Label(foot, text="Нууц үг солих", font=(FONT, 10, "underline"), fg="#0969da", bg=BG,
                            cursor="hand2")
            link.pack(side="right", padx=16)
            link.bind("<Button-1>", lambda _: self.change_password())
        self.refresh()

    def refresh(self) -> None:
        s = self.app.tracker.snapshot()
        color = GREEN if s["status"] == WORKING else RED
        for w in (self.banner, self.status_lbl, self.timer_lbl, self.timer_caption):
            w.config(bg=color)
        self.status_lbl.config(text=f"● {s['status_text']}")
        self.timer_lbl.config(text=_hms(s["work_seconds"]))
        self.rows["away"].config(text=fmt_dur(s["away_seconds"]))
        self.rows["app"].config(text=(s["app"] or "-")[:32])
        if not s["camera_ok"]:
            cam = "Олдсонгүй (гар/хулганаар шалгана)"
        else:
            cam = "Холбогдсон · " + ("царай харагдаж байна" if s["face_visible"] else "царай харагдахгүй")
        self.rows["cam"].config(text=cam)
        self.rows["cloud"].config(text=self.app.cloud_status())
        for i, (a, b) in enumerate(self.app_rows):
            if i < len(s["top_apps"]):
                name, sec = s["top_apps"][i]
                a.config(text=name[:28])
                b.config(text=fmt_dur(sec))
            else:
                a.config(text="")
                b.config(text="")
        self._job = self.root.after(1000, self.refresh)

    def open_dashboard(self) -> None:
        from .dashboard import Dashboard

        if self.dashboard is not None and self.dashboard.winfo_exists():
            self.dashboard.lift()
            self.dashboard.refresh()
            return
        self.dashboard = Dashboard(self.root, self.app.supa, self.app.profile, self.bg)

    def open_report(self) -> None:
        self.app.tracker.flush_now()
        cfg = self.app.cfg
        r = build_report(self.app.storage, date.today().isoformat(), cfg["employee_name"], cfg["min_away_period_min"])
        html_path, _ = save_report(r)
        webbrowser.open(html_path.as_uri())

    def send_report(self) -> None:
        self.send_btn.config(state="disabled", text="Илгээж байна…")
        self.bg.run(self.app.send_today, self._sent)

    def _sent(self, result) -> None:
        ok, msg = result
        self.send_btn.config(state="normal", text="Илгээх")
        (messagebox.showinfo if ok else messagebox.showwarning)(APP_TITLE, msg, parent=self.root)

    def change_password(self) -> None:
        pw = simpledialog.askstring(APP_TITLE, "Шинэ нууц үг (6+ тэмдэгт):", show="•", parent=self.root)
        if not pw:
            return
        if len(pw) < 6:
            return messagebox.showwarning(APP_TITLE, "Нууц үг хамгийн багадаа 6 тэмдэгт", parent=self.root)
        if simpledialog.askstring(APP_TITLE, "Шинэ нууц үгээ давтана уу:", show="•", parent=self.root) != pw:
            return messagebox.showwarning(APP_TITLE, "Нууц үг таарахгүй байна", parent=self.root)
        self.bg.run(lambda: self.app.supa.update_password(pw),
                    lambda _: messagebox.showinfo(APP_TITLE, "Нууц үг солигдлоо", parent=self.root),
                    lambda e: messagebox.showerror(APP_TITLE, friendly(e), parent=self.root))

    def _teardown(self) -> None:
        if self._job:
            self.root.after_cancel(self._job)
            self._job = None
        if self.dashboard is not None and self.dashboard.winfo_exists():
            self.dashboard.close()

    def logout(self) -> None:
        if not messagebox.askokcancel(APP_TITLE, "Гарахад цаг бүртгэл зогсоно. Гарах уу?", parent=self.root):
            return
        self._teardown()
        self.on_logout()

    def on_close(self) -> None:
        if messagebox.askokcancel(APP_TITLE, "Програмыг хаавал цаг бүртгэл зогсоно.\nХаах уу?", parent=self.root):
            self._teardown()
            self.app.stop()
            self.root.destroy()


def run_gui(cfg) -> None:
    root = tk.Tk()
    root.configure(bg=BG)
    bg = Bg(root)
    state: dict = {}

    def on_ready(supa: SupaClient, profile: dict) -> None:
        cfg["employee_name"] = profile.get("full_name") or profile.get("email") or ""
        app = App(cfg, supa=supa, profile=profile)
        app.start()  # main thread дээр (macOS камерын зөвшөөрөл)
        state["app"] = app
        MainWindow(root, app, bg, on_logout)

    def on_logout() -> None:
        app: App = state.pop("app")
        root.protocol("WM_DELETE_WINDOW", on_close_auth)
        auth._busy("Гарч байна…")

        def work():
            app.stop()  # "Офлайн" илгээнэ
            app.supa.sign_out()

        bg.run(work, lambda _: auth.show_login(), lambda _: auth.show_login())

    def on_close_auth() -> None:
        if "app" in state:  # MainWindow өөрийн handler-тэй
            return
        root.destroy()

    auth = AuthScreen(root, cfg, bg, on_ready)
    root.protocol("WM_DELETE_WINDOW", on_close_auth)
    auth.start()
    try:
        root.mainloop()
    finally:
        if "app" in state:
            state["app"].stop()
