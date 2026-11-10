"""Үндсэн цонх: нэвтрэх → "Ажиллаж байна" / "Ажлаас гарсан" төлөв, таймер, аппууд, самбар."""
from __future__ import annotations

import logging
import sys
import threading
import time
import tkinter as tk
import webbrowser
from datetime import date
from tkinter import messagebox, simpledialog

from . import APP_TITLE, updater
from .app import App
from .login import AuthScreen
from .notify import channels_enabled
from .report import build_report, save_report
from .supa import ROLE_TEXT, SupaClient
from .tracker import WORKING
from .uiutil import BG, FG, FONT, GREEN, MUTED, RED, Bg, button, clear, fmt_dur, friendly

log = logging.getLogger(__name__)
TOP_N = 6
CAMERA_WARN = {"blocked": "⚠ Камер таглагдсан байна", "static": "⚠ Хөдөлгөөнгүй царай (зураг?)"}


class Tray:
    """Цонх хаахад програм арын горимд үргэлжилнэ.
    Windows: системийн tray дүрс (pystray).  macOS: Dock дүрс дээр дарж нээнэ, ⌘Q бүрэн хаана."""

    COLORS = {"working": (26, 127, 55), "away": (207, 34, 46), "offline": (140, 149, 159)}

    def __init__(self, root: tk.Tk, bg: Bg, on_show, on_quit):
        self.icon = None
        self._status = None
        if sys.platform == "win32":
            try:
                import pystray

                menu = pystray.Menu(pystray.MenuItem("Нээх", lambda: bg.call_soon(on_show), default=True),
                                    pystray.MenuItem("Програмыг хаах", lambda: bg.call_soon(on_quit)))
                self.icon = pystray.Icon("AjliinButeemj", self._image("offline"), APP_TITLE, menu)
                threading.Thread(target=self.icon.run, name="tray", daemon=True).start()
            except Exception:  # noqa: BLE001
                log.exception("Tray дүрс үүсгэж чадсангүй")
                self.icon = None
        elif sys.platform == "darwin":
            root.createcommand("tk::mac::ReopenApplication", on_show)
            root.createcommand("tk::mac::Quit", lambda: on_quit(confirm=False))

    @property
    def available(self) -> bool:
        return self.icon is not None or sys.platform == "darwin"

    def _image(self, status):
        from PIL import Image, ImageDraw

        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.ellipse((4, 4, 60, 60), fill=self.COLORS.get(status, self.COLORS["offline"]) + (255,))
        d.ellipse((22, 22, 42, 42), fill=(255, 255, 255, 255))
        return img

    def set_status(self, status: str, text: str) -> None:
        if self.icon is None or status == self._status:
            return
        self._status = status
        try:
            self.icon.icon = self._image(status)
            self.icon.title = f"{APP_TITLE} — {text}"
        except Exception:  # noqa: BLE001
            pass

    def notify(self, text: str) -> None:
        if self.icon is not None:
            try:
                self.icon.notify(text, APP_TITLE)
            except Exception:  # noqa: BLE001
                pass

    def stop(self) -> None:
        if self.icon is not None:
            try:
                self.icon.stop()
            except Exception:  # noqa: BLE001
                pass


def _hms(sec: float) -> str:
    sec = int(sec)
    return f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


class MainWindow:
    def __init__(self, root: tk.Tk, app: App, bg: Bg, on_logout, tray: Tray | None = None, on_quit=None):
        self.root, self.app, self.bg, self.on_logout = root, app, bg, on_logout
        self.tray, self.on_quit = tray, on_quit
        self.dashboard = None
        self._job = None
        self._hidden_once = False
        self._update_info = None
        self._last_update_check = time.monotonic()  # эхлэхэд run_gui шалгасан
        clear(root)
        root.title(APP_TITLE)
        root.configure(bg=BG)
        root.resizable(False, False)
        if app.cfg.get("always_on_top"):
            root.attributes("-topmost", True)
        root.protocol("WM_DELETE_WINDOW", self.hide if tray and tray.available else self.on_close)

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
        self.update_btn = button(root, "", self.install_update, primary=True)
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
        if s.get("camera_state") in CAMERA_WARN:
            cam = CAMERA_WARN[s["camera_state"]]
        self.rows["cam"].config(text=cam, fg="#bc4c00" if s.get("camera_state") in CAMERA_WARN else FG)
        if self.tray:
            self.tray.set_status(s["status"], s["status_text"])
        if updater.supported() and self.app.cfg.get("auto_update", True) and \
                time.monotonic() - self._last_update_check > updater.CHECK_EVERY_SEC:
            self._last_update_check = time.monotonic()
            self.bg.run(lambda: updater.check(self.app.cfg.get("update_repo") or updater.DEFAULT_REPO),
                        self._update_found, lambda e: log.info("Шинэчлэл шалгаж чадсангүй: %s", e))
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

    def _update_found(self, info) -> None:
        if not info:
            return
        self._update_info = info
        self.update_btn.config(text=f"⬇ Шинэ хувилбар {info['version']} — Шинэчлэх")
        self.update_btn.pack(fill="x", padx=16, pady=(0, 8))
        if self.tray:
            self.tray.notify(f"Шинэ хувилбар {info['version']} бэлэн боллоо")

    def install_update(self) -> None:
        info = self._update_info
        if not info or not messagebox.askokcancel(
                APP_TITLE, f"{info['version']} хувилбар руу шинэчлэх үү?\nПрограм хаагдаад дахин нээгдэнэ.",
                parent=self.root):
            return
        self.update_btn.config(text="Татаж байна…", state="disabled")

        def progress(done, total):
            if total:
                self.bg.call_soon(lambda: self.update_btn.config(text=f"Татаж байна… {done * 100 // total}%"))

        def work():
            path = updater.download(info, progress)
            return path

        def ready(path):
            self._teardown()
            self.app.stop()
            from .system import release_single_instance

            release_single_instance()
            try:
                updater.apply(path)
            except Exception as exc:  # noqa: BLE001
                messagebox.showerror(APP_TITLE, f"Шинэчилж чадсангүй: {exc}", parent=self.root)
            if self.tray:
                self.tray.stop()
            self.root.destroy()

        self.bg.run(work, ready, lambda e: (self.update_btn.config(text=f"Татаж чадсангүй: {friendly(e)[:40]}",
                                                                   state="normal")))

    def hide(self) -> None:
        """Цонхыг нууна — цаг бүртгэл үргэлжилнэ."""
        if not self._hidden_once:
            self._hidden_once = True
            if sys.platform == "win32":
                self.tray.notify("Програм арын горимд ажилласаар байна. Tray дүрс дээр дарж нээнэ.")
            else:
                messagebox.showinfo(APP_TITLE, "Цонх нуугдлаа, цаг бүртгэл үргэлжилж байна.\n"
                                               "Dock дээрх дүрсэн дээр дарж дахин нээнэ. Бүрэн хаах бол ⌘Q.",
                                    parent=self.root)
        self.root.withdraw()

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

    def on_close(self, confirm: bool = True) -> None:
        if confirm and not messagebox.askokcancel(APP_TITLE, "Програмыг хаавал цаг бүртгэл зогсоно.\nХаах уу?",
                                                  parent=self.root):
            return
        self._teardown()
        self.app.stop()
        if self.tray:
            self.tray.stop()
        self.root.destroy()


def _startup_update(auth: AuthScreen, bg: Bg, root: tk.Tk, tray: Tray, cfg) -> None:
    """Эхлэхэд шинэ хувилбар байвал татаж суулгана. Алдаа/интернэтгүй бол шууд нэвтрэх рүү."""
    auth._busy("Шинэчлэл шалгаж байна…")

    def found(info):
        if not info:
            return auth.start()
        f = auth._frame(f"Шинэ хувилбар {info['version']}", "Татаж суулгаад програм дахин нээгдэнэ.")
        lbl = tk.Label(f, text="Татаж байна…", font=(FONT, 12), fg=FG, bg=BG)
        lbl.pack(anchor="w")

        def progress(done, total):
            if total:
                bg.call_soon(lambda: lbl.winfo_exists() and lbl.config(text=f"Татаж байна… {done * 100 // total}%"))

        def ready(path):
            from .system import release_single_instance

            release_single_instance()
            try:
                updater.apply(path)
            except Exception as exc:  # noqa: BLE001
                log.exception("Шинэчлэл суулгаж чадсангүй")
                messagebox.showerror(APP_TITLE, f"Шинэчилж чадсангүй: {exc}")
                return auth.start()
            tray.stop()
            root.destroy()

        bg.run(lambda: updater.download(info, progress), ready,
               lambda e: (log.warning("Шинэчлэл татаж чадсангүй: %s", e), auth.start()))

    bg.run(lambda: updater.check(cfg.get("update_repo") or updater.DEFAULT_REPO), found,
           lambda e: (log.info("Шинэчлэл шалгаж чадсангүй: %s", e), auth.start()))


def run_gui(cfg, updated: bool = False) -> None:
    from .system import start_instance_listener

    root = tk.Tk()
    root.configure(bg=BG)
    bg = Bg(root)
    state: dict = {}

    def show() -> None:
        root.deiconify()
        root.lift()
        try:
            root.focus_force()
        except tk.TclError:
            pass

    def quit_app(confirm: bool = True) -> None:
        main = state.get("main")
        if main is not None:
            main.on_close(confirm=confirm)
        else:
            tray.stop()
            root.destroy()

    tray = Tray(root, bg, show, quit_app)
    start_instance_listener(lambda: bg.call_soon(show))

    def on_ready(supa: SupaClient, profile: dict) -> None:
        cfg["employee_name"] = profile.get("full_name") or profile.get("email") or ""
        app = App(cfg, supa=supa, profile=profile)
        app.start()  # main thread дээр (macOS камерын зөвшөөрөл)
        state["app"] = app
        state["main"] = MainWindow(root, app, bg, on_logout, tray, quit_app)

    def on_logout() -> None:
        state.pop("main", None)
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
        tray.stop()
        root.destroy()

    auth = AuthScreen(root, cfg, bg, on_ready)
    root.protocol("WM_DELETE_WINDOW", on_close_auth)
    updater.cleanup()
    if updater.supported() and cfg.get("auto_update", True) and not updated:
        _startup_update(auth, bg, root, tray, cfg)
    else:
        auth.start()
    try:
        root.mainloop()
    finally:
        if "app" in state:
            state["app"].stop()
        tray.stop()
