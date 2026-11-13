"""Үндсэн цонх ба tray: "Ажиллаж байна" / "Ажлаас гарсан" төлөв, таймер, аппууд, (админд) самбар.

Нэвтрэх цонх байхгүй: ажилтныг Windows хэрэглэгч + компьютерийн нэрээр автоматаар таньж, нэрийг нь анх нэг удаа асууна.
Автоматаар эхлэхэд (--background) цонх нээгдэхгүй — зөвхөн tray дүрс.
"""
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
from .config import check_password, load_admin, load_share, save_share
from .notify import channels_enabled
from .report import build_report, save_report
from .share import ShareClient, default_name, make_uid
from .tracker import WORKING
from .uiutil import BG, FG, FONT, GREEN, MUTED, RED, Bg, button, clear, fmt_dur, friendly

log = logging.getLogger(__name__)
TOP_N = 6
CAMERA_WARN = {"blocked": "⚠ Камер таглагдсан байна", "static": "⚠ Хөдөлгөөнгүй царай (зураг?)"}
PAUSED_COLOR = "#9a6700"


class Tray:
    """Цонх хаахад програм арын горимд үргэлжилнэ.
    Windows: системийн tray дүрс (pystray).  macOS: Dock дүрс дээр дарж нээнэ, ⌘Q бүрэн хаана."""

    COLORS = {"working": (26, 127, 55), "away": (207, 34, 46), "offline": (140, 149, 159), "paused": (154, 103, 0)}

    def __init__(self, root: tk.Tk, bg: Bg, on_show, on_quit, on_dashboard=None, on_pause=None, is_paused=None):
        self.icon = None
        self._status = None
        if sys.platform == "win32":
            try:
                import pystray

                items = [pystray.MenuItem("Нээх", lambda: bg.call_soon(on_show), default=True)]
                if on_pause and is_paused:
                    items.append(pystray.MenuItem(lambda _: "▶ Үргэлжлүүлэх" if is_paused() else "⏸ Зогсоох",
                                                  lambda: bg.call_soon(on_pause)))
                if on_dashboard:
                    items.append(pystray.MenuItem("Самбар (админ)", lambda: bg.call_soon(on_dashboard)))
                items.append(pystray.MenuItem("Програмыг хаах", lambda: bg.call_soon(on_quit)))
                self.icon = pystray.Icon("AjliinButeemj", self._image("offline"), APP_TITLE, pystray.Menu(*items))
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

    def update_menu(self) -> None:
        if self.icon is not None:
            try:
                self.icon.update_menu()
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
    def __init__(self, root: tk.Tk, app: App, bg: Bg, tray: Tray | None = None, admin: bool = False):
        self.root, self.app, self.bg, self.tray, self.admin = root, app, bg, tray, admin
        self.dashboard = None
        self._admin_unlocked = False
        self._job = None
        self._hidden_once = False
        self._update_info = None
        self._installing = False
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
        for i, (key, caption) in enumerate([("name", "Ажилтан"), ("away", "Ажлаас гарсан"), ("app", "Одоогийн апп"),
                                            ("cam", "Камер"), ("cloud", "Админы хавтас")]):
            tk.Label(body, text=caption, font=(FONT, 11), fg=MUTED, bg=BG).grid(row=i, column=0, sticky="w", pady=2)
            lbl = tk.Label(body, text="", font=(FONT, 11), fg=FG, bg=BG, anchor="w", width=34)
            lbl.grid(row=i, column=1, sticky="w", padx=(10, 0), pady=2)
            self.rows[key] = lbl
        self.rows["name"].config(text=app.cfg["employee_name"] or "-")

        tk.Label(body, text="Өнөөдрийн аппууд", font=(FONT, 12, "bold"), fg=FG, bg=BG).grid(
            row=5, column=0, columnspan=2, sticky="w", pady=(12, 4))
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
        if admin and app.api:
            button(btns, "Самбар", self.open_dashboard, primary=True).pack(side="left", padx=(0, 8))
        self.pause_btn = button(btns, "⏸ Зогсоох", self.toggle_pause)
        self.pause_btn.pack(side="left", padx=(0, 8))
        button(btns, "Тайлан", self.open_report).pack(side="left")
        if channels_enabled(app.cfg):
            self.send_btn = button(btns, "Илгээх", self.send_report)
            self.send_btn.pack(side="left", padx=(8, 0))
        foot = tk.Frame(root, bg=BG, pady=6)
        foot.pack(fill="x")
        tk.Label(foot, text="Дэлгэц бичихгүй · Зураг хадгалахгүй", font=(FONT, 10), fg=MUTED, bg=BG).pack(
            side="left", padx=16)
        link = tk.Label(foot, text="Нэр солих", font=(FONT, 10, "underline"), fg="#0969da", bg=BG, cursor="hand2")
        link.pack(side="right", padx=16)
        link.bind("<Button-1>", lambda _: self.change_name())
        # ✕ нь tray руу нуудаг; бүрэн хаахад доорх улаан товч (эсвэл tray → "Програмыг хаах")
        self.exit_btn = button(root, "Програмаас гарах", self.on_close, danger=True, font=(FONT, 11, "bold"))
        self.exit_btn.pack(fill="x", padx=16, pady=(0, 12), ipady=2)
        self.update_btn = button(root, "", self.install_update, primary=True)
        self.refresh()

    def refresh(self) -> None:
        s = self.app.tracker.snapshot()
        color = PAUSED_COLOR if s["paused"] else (GREEN if s["status"] == WORKING else RED)
        for w in (self.banner, self.status_lbl, self.timer_lbl, self.timer_caption):
            w.config(bg=color)
        self.status_lbl.config(text=f"{'⏸' if s['paused'] else '●'} {s['status_text']}")
        self.pause_btn.config(text="▶ Үргэлжлүүлэх" if s["paused"] else "⏸ Зогсоох")
        self.timer_lbl.config(text=_hms(s["work_seconds"]))
        self.rows["away"].config(text=fmt_dur(s["away_seconds"]))
        self.rows["app"].config(text=(s["app"] or "-")[:32])
        if s["paused"]:
            cam = "Унтраасан (зогсоосон үед)"
        elif not s["camera_ok"]:
            cam = "Олдсонгүй (гар/хулганаар шалгана)"
        else:
            cam = "Холбогдсон · " + ("царай харагдаж байна" if s["face_visible"] else "царай харагдахгүй")
        if s.get("camera_state") in CAMERA_WARN:
            cam = CAMERA_WARN[s["camera_state"]]
        self.rows["cam"].config(text=cam, fg="#bc4c00" if s.get("camera_state") in CAMERA_WARN else FG)
        if self.tray:
            self.tray.set_status("paused" if s["paused"] else s["status"], s["status_text"])
        if updater.supported() and self.app.cfg.get("auto_update", True) and \
                time.monotonic() - self._last_update_check > updater.CHECK_EVERY_SEC:
            self._last_update_check = time.monotonic()
            self.check_update(auto=self.root.state() == "withdrawn")
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

    # ------------------------------------------------------------------ шинэчлэл
    def check_update(self, auto: bool = False) -> None:
        """auto=True (эхлэх үед, эсвэл цонх нуугдсан үед): асуулгүйгээр татаж суулгана."""
        self.bg.run(lambda: updater.check_share(self.app.api),
                    lambda info: self._update_found(info, auto),
                    lambda e: log.info("Шинэчлэл шалгаж чадсангүй: %s", e))

    def _update_found(self, info, auto: bool = False) -> None:
        if not info:
            return
        self._update_info = info
        if auto:
            return self._install(info)
        self.update_btn.config(text=f"⬇ Шинэ хувилбар {info['version']} — Шинэчлэх")
        self.update_btn.pack(fill="x", padx=16, pady=(0, 8), before=self.exit_btn)
        if self.tray:
            self.tray.notify(f"Шинэ хувилбар {info['version']} бэлэн боллоо")

    def install_update(self) -> None:
        info = self._update_info
        if not info or not messagebox.askokcancel(
                APP_TITLE, f"{info['version']} хувилбар руу шинэчлэх үү?\nПрограм хаагдаад дахин нээгдэнэ.",
                parent=self.root):
            return
        self._install(info)

    def _install(self, info) -> None:
        if self._installing:
            return
        self._installing = True
        self.update_btn.config(text="Татаж байна…", state="disabled")

        def progress(done, total):
            if total:
                self.bg.call_soon(lambda: self.update_btn.config(text=f"Татаж байна… {done * 100 // total}%"))

        def ready(path):
            self._teardown()
            self.app.stop()
            from .system import release_single_instance

            release_single_instance()
            try:
                updater.apply(path)
            except Exception as exc:  # noqa: BLE001
                log.exception("Шинэчлэл суулгаж чадсангүй")
                messagebox.showerror(APP_TITLE, f"Шинэчилж чадсангүй: {exc}", parent=self.root)
            if self.tray:
                self.tray.stop()
            self.root.destroy()

        def failed(e):
            self._installing = False
            log.warning("Шинэчлэл татаж чадсангүй: %s", e)
            self.update_btn.config(text=f"Татаж чадсангүй: {friendly(e)[:40]}", state="normal")

        self.bg.run(lambda: updater.download(info, progress), ready, failed)

    # ------------------------------------------------------------------ үйлдлүүд
    def hide(self, quiet: bool = False) -> None:
        """Цонхыг нууна — цаг бүртгэл үргэлжилнэ."""
        if not self._hidden_once and not quiet:
            if sys.platform == "win32":
                self.tray.notify("Програм арын горимд ажилласаар байна. Tray дүрс дээр дарж нээнэ.")
            else:
                messagebox.showinfo(APP_TITLE, "Цонх нуугдлаа, цаг бүртгэл үргэлжилж байна.\n"
                                               "Dock дээрх дүрсэн дээр дарж дахин нээнэ. Бүрэн хаах бол ⌘Q.",
                                    parent=self.root)
        self._hidden_once = True
        self.root.withdraw()

    def open_dashboard(self) -> None:
        if not (self.admin and self.app.api):
            return
        if self.dashboard is not None and self.dashboard.winfo_exists():
            self.dashboard.deiconify()
            self.dashboard.lift()
            self.dashboard.refresh()
            return
        if not self._admin_unlocked:
            admin = load_admin() or {}
            pw = simpledialog.askstring(APP_TITLE, "Админ нууц үг:", show="•", parent=self.root)
            if pw is None:
                return
            if not check_password(pw, admin.get("password", "")):
                return messagebox.showerror(APP_TITLE, "Нууц үг буруу байна", parent=self.root)
            self._admin_unlocked = True
            self.app.api.is_admin = True
        from .dashboard import Dashboard

        self.dashboard = Dashboard(self.root, self.app.api, {"full_name": "Админ", "role": "admin"}, self.bg)

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

    def toggle_pause(self) -> None:
        """Гараар зогсоох / үргэлжлүүлэх. Зогссон хугацааг "гарсан"-д тооцно."""
        self.app.tracker.set_paused(not self.app.tracker.paused)
        if self.tray:
            self.tray.update_menu()
            self.tray.notify("Цаг тоолох зогслоо. \"Үргэлжлүүлэх\" дарж эхлүүлнэ." if self.app.tracker.paused
                             else "Цаг тоолох үргэлжиллээ.")
        if self._job:
            self.root.after_cancel(self._job)
        self.refresh()

    def change_name(self) -> None:
        name = ask_name(self.root, self.app.cfg["employee_name"])
        if not name:
            return
        self.app.cfg["employee_name"] = name
        self.app.cfg.save()
        if self.app.api:
            self.app.api.set_name(name)
            if self.app.cloud:
                self.app.cloud.poke()
        self.rows["name"].config(text=name)

    def _teardown(self) -> None:
        if self._job:
            self.root.after_cancel(self._job)
            self._job = None
        if self.dashboard is not None and self.dashboard.winfo_exists():
            self.dashboard.close()

    def on_close(self, confirm: bool = True) -> None:
        if confirm and not messagebox.askokcancel(APP_TITLE, "Програмыг хаавал цаг бүртгэл зогсоно.\nХаах уу?",
                                                  parent=self.root):
            return
        self._teardown()
        self.app.stop()  # эцсийн JSON-г админы хавтсанд бичнэ
        if self.tray:
            self.tray.stop()
        self.root.destroy()


def ask_name(root: tk.Misc, current: str = "") -> str | None:
    while True:
        name = simpledialog.askstring(APP_TITLE, "Таны овог нэр (самбар, тайланд харагдана):",
                                      initialvalue=current or default_name(), parent=root)
        if name is None:
            return None
        name = name.strip()[:100]
        if name:
            return name


def ask_share(root: tk.Misc) -> dict | None:
    """share.json байхгүй үед (заавал биш): админы хавтасны замыг гараар оруулна."""
    path = simpledialog.askstring(
        APP_TITLE, "Админы хуваалцсан хавтасны зам (жишээ: \\\\ADMIN-PC\\AjliinButeemj).\n"
                   "Админ share.json-г програмд багцалсан бол энэ асуулт гарахгүй.\n"
                   "Хоосон орхивол зөвхөн энэ компьютерт хадгална.", parent=root)
    if not path or not path.strip():
        return None
    info = {"share": path.strip().rstrip("\\/")}
    save_share(info)
    return info


def run_gui(cfg, updated: bool = False, background: bool = False) -> None:
    from .system import start_instance_listener

    root = tk.Tk()
    root.withdraw()  # эхний асуултуудын үед хоосон цонх харагдуулахгүй
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

    admin = load_admin() is not None
    tray = Tray(root, bg, show, quit_app,
                on_dashboard=(lambda: state["main"].open_dashboard()) if admin else None,
                on_pause=lambda: state["main"].toggle_pause() if "main" in state else None,
                is_paused=lambda: "app" in state and state["app"].tracker.paused)
    start_instance_listener(lambda: bg.call_soon(show))

    first_run = not cfg["employee_name"]
    if first_run:
        name = ask_name(root)
        if not name:
            tray.stop()
            root.destroy()
            return
        cfg["employee_name"] = name
        cfg.save()
    info = load_share() or (ask_share(root) if first_run else None)
    api = ShareClient(info, make_uid(), cfg["employee_name"]) if info else None

    app = App(cfg, api=api, admin=admin)
    app.start()  # main thread дээр (macOS камерын зөвшөөрөл)
    state["app"] = app
    main = state["main"] = MainWindow(root, app, bg, tray, admin=admin)
    if background and not first_run:
        main.hide(quiet=True)
    else:
        show()

    updater.cleanup()
    if updater.supported() and cfg.get("auto_update", True) and not updated:
        main.check_update(auto=True)
    try:
        root.mainloop()
    finally:
        app.stop()
        tray.stop()
