"""Tkinter цонх: "Ажиллаж байна" / "Ажлаас гарсан" төлөв, таймер, топ аппууд."""
from __future__ import annotations

import sys
import threading
import tkinter as tk
import webbrowser
from datetime import date
from tkinter import messagebox, simpledialog

from . import APP_TITLE
from .app import App
from .report import build_report, fmt_dur, save_report
from .tracker import WORKING

FONT = "Segoe UI" if sys.platform == "win32" else "Helvetica"
BG = "#ffffff"
FG = "#1f2328"
MUTED = "#656d76"
GREEN = "#1a7f37"
RED = "#cf222e"
TOP_N = 6


def _hms(sec: float) -> str:
    sec = int(sec)
    return f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def ask_employee_name(root: tk.Tk | None = None) -> str:
    return (
        simpledialog.askstring(APP_TITLE, "Таны нэр (тайлан дээр гарна):", parent=root) or ""
    ).strip()


class MainWindow:
    def __init__(self, root: tk.Tk, app: App):
        self.root = root
        self.app = app
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
        self.timer_caption = tk.Label(
            self.banner, text="Өнөөдөр ажилласан", font=(FONT, 11), fg="white", bg=GREEN
        )
        self.timer_caption.pack(anchor="w")

        # --- мэдээлэл
        body = tk.Frame(root, bg=BG, padx=16, pady=10)
        body.pack(fill="both", expand=True)
        self.rows: dict[str, tk.Label] = {}
        for i, (key, caption) in enumerate(
            [
                ("name", "Ажилтан"),
                ("away", "Ажлаас гарсан"),
                ("app", "Одоогийн апп"),
                ("cam", "Камер"),
                ("cloud", "Вэб самбар"),
            ]
        ):
            tk.Label(body, text=caption, font=(FONT, 11), fg=MUTED, bg=BG).grid(row=i, column=0, sticky="w", pady=2)
            lbl = tk.Label(body, text="", font=(FONT, 11), fg=FG, bg=BG, anchor="w", width=26)
            lbl.grid(row=i, column=1, sticky="w", padx=(10, 0), pady=2)
            self.rows[key] = lbl
        self.rows["name"].config(text=app.cfg["employee_name"] or "-")

        tk.Label(body, text="Өнөөдрийн аппууд", font=(FONT, 12, "bold"), fg=FG, bg=BG).grid(
            row=5, column=0, columnspan=2, sticky="w", pady=(12, 4)
        )
        self.app_rows: list[tuple[tk.Label, tk.Label]] = []
        for i in range(TOP_N):
            a = tk.Label(body, text="", font=(FONT, 11), fg=FG, bg=BG, anchor="w")
            b = tk.Label(body, text="", font=(FONT, 11), fg=MUTED, bg=BG, anchor="e")
            a.grid(row=6 + i, column=0, sticky="w")
            b.grid(row=6 + i, column=1, sticky="e")
            self.app_rows.append((a, b))

        # --- товчнууд
        btns = tk.Frame(root, bg=BG, padx=16, pady=8)
        btns.pack(fill="x")
        tk.Button(btns, text="Тайлан харах", command=self.open_report).pack(side="left")
        self.send_btn = tk.Button(btns, text="Менежерт илгээх", command=self.send_report)
        self.send_btn.pack(side="right")
        tk.Label(
            root, text="Дэлгэц бичихгүй · Зураг хадгалахгүй", font=(FONT, 10), fg=MUTED, bg=BG, pady=6
        ).pack()

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
        self.root.after(1000, self.refresh)

    def open_report(self) -> None:
        self.app.tracker.flush_now()
        cfg = self.app.cfg
        r = build_report(self.app.storage, date.today().isoformat(), cfg["employee_name"], cfg["min_away_period_min"])
        html_path, _ = save_report(r)
        webbrowser.open(html_path.as_uri())

    def send_report(self) -> None:
        self.send_btn.config(state="disabled", text="Илгээж байна…")

        def work():
            ok, msg = self.app.send_today()
            self.root.after(0, lambda: self._sent(ok, msg))

        threading.Thread(target=work, daemon=True).start()

    def _sent(self, ok: bool, msg: str) -> None:
        self.send_btn.config(state="normal", text="Менежерт илгээх")
        (messagebox.showinfo if ok else messagebox.showwarning)(APP_TITLE, msg, parent=self.root)

    def on_close(self) -> None:
        if messagebox.askokcancel(APP_TITLE, "Програмыг хаавал цаг бүртгэл зогсоно.\nХаах уу?", parent=self.root):
            self.app.stop()
            self.root.destroy()


def run_gui(cfg) -> None:
    root = tk.Tk()
    root.withdraw()
    if not cfg["employee_name"]:
        name = ask_employee_name(root)
        if name:
            cfg["employee_name"] = name
            cfg.save()
    app = App(cfg)
    app.start()  # камерыг main thread дээр нээнэ (macOS зөвшөөрөл)
    MainWindow(root, app)
    root.deiconify()
    try:
        root.mainloop()
    finally:
        app.stop()
