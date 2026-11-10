"""Нэвтрэх урсгал: сервер тохиргоо → нэвтрэх / бүртгүүлэх → (хүлээгдэж буй) → програм."""
from __future__ import annotations

import logging
import tkinter as tk

from . import APP_TITLE
from .config import clear_server, data_dir, load_server, save_server
from .supa import ROLE_TEXT, NetworkError, SupaClient
from .uiutil import BG, FG, FONT, MUTED, RED, Bg, button, clear, friendly

log = logging.getLogger(__name__)
PENDING_RECHECK_MS = 20000


class AuthScreen:
    def __init__(self, root: tk.Tk, cfg: dict, bg: Bg, on_ready):
        self.root = root
        self.cfg = cfg
        self.bg = bg
        self.on_ready = on_ready  # callback(supa, profile)
        self.supa: SupaClient | None = None
        self._pending_job = None

    # ------------------------------------------------------------------ эхлэл
    def start(self) -> None:
        server = load_server(self.cfg)
        if not server:
            return self.show_server()
        self._make_client(*server)
        self._busy("Нэвтэрч байна…")
        self.bg.run(self.supa.restore, lambda ok: self.check_profile() if ok else self.show_login())

    def _make_client(self, url: str, key: str) -> None:
        self.supa = SupaClient(url, key, data_dir() / "session.json")

    # ------------------------------------------------------------------ дэлгэцүүд
    def _frame(self, title: str, subtitle: str = "") -> tk.Frame:
        self._cancel_pending()
        clear(self.root)
        self.root.title(APP_TITLE)
        self.root.configure(bg=BG)
        self.root.resizable(False, False)
        f = tk.Frame(self.root, bg=BG, padx=28, pady=24)
        f.pack(fill="both", expand=True)
        tk.Label(f, text=APP_TITLE, font=(FONT, 20, "bold"), fg=FG, bg=BG).pack(anchor="w")
        tk.Label(f, text=title, font=(FONT, 13), fg=FG, bg=BG).pack(anchor="w", pady=(4, 0))
        if subtitle:
            tk.Label(f, text=subtitle, font=(FONT, 11), fg=MUTED, bg=BG, wraplength=320, justify="left").pack(
                anchor="w", pady=(2, 0))
        tk.Frame(f, height=14, bg=BG).pack()
        return f

    def _field(self, parent, label: str, show: str = "") -> tk.Entry:
        tk.Label(parent, text=label, font=(FONT, 11), fg=MUTED, bg=BG).pack(anchor="w")
        e = tk.Entry(parent, font=(FONT, 13), width=30, show=show)
        e.pack(anchor="w", pady=(0, 10), ipady=3)
        return e

    def _busy(self, text: str) -> None:
        f = self._frame(text)
        tk.Label(f, text="Түр хүлээнэ үү…", font=(FONT, 11), fg=MUTED, bg=BG).pack(anchor="w")

    def show_server(self) -> None:
        f = self._frame("Сервер тохируулах",
                        "Supabase → Project Settings → API Keys хэсгээс авна. "
                        "Админ server.json-оор програмд багцалсан бол энэ цонх гарахгүй.")
        url = self._field(f, "Project URL")
        key = self._field(f, "Publishable (anon) key")
        err = tk.Label(f, text="", font=(FONT, 11), fg=RED, bg=BG, wraplength=320, justify="left")

        def save():
            u, k = url.get().strip().rstrip("/"), key.get().strip()
            if not u.startswith("https://") or not k:
                err.config(text="URL (https://...) болон key-г бөглөнө үү")
                return
            save_server(u, k)
            self._make_client(u, k)
            self.show_login()

        button(f, "Хадгалах", save, primary=True).pack(anchor="w")
        err.pack(anchor="w", pady=(8, 0))
        url.focus_set()

    def show_login(self, mode: str = "login", message: str = "") -> None:
        register = mode == "register"
        f = self._frame("Бүртгүүлэх" if register else "Нэвтрэх",
                        "Бүртгүүлсний дараа админ таны эрхийг баталгаажуулна." if register else "")
        name = self._field(f, "Овог нэр") if register else None
        email = self._field(f, "Имэйл")
        pw = self._field(f, "Нууц үг", show="•")
        pw2 = self._field(f, "Нууц үг давтах", show="•") if register else None
        err = tk.Label(f, text=message, font=(FONT, 11), fg=RED, bg=BG, wraplength=320, justify="left")

        def submit(_=None):
            e, p = email.get().strip(), pw.get()
            if register:
                n = name.get().strip()
                if not n or not e or not p:
                    return err.config(text="Бүх талбарыг бөглөнө үү")
                if len(p) < 6:
                    return err.config(text="Нууц үг хамгийн багадаа 6 тэмдэгт")
                if p != pw2.get():
                    return err.config(text="Нууц үг таарахгүй байна")
                fn = lambda: self.supa.sign_up(e, p, n)  # noqa: E731
            else:
                if not e or not p:
                    return err.config(text="Имэйл, нууц үгээ оруулна уу")
                fn = lambda: self.supa.sign_in(e, p) or True  # noqa: E731
            btn.config(state="disabled")
            err.config(text="")

            def done(signed_in):
                if signed_in:
                    self.check_profile()
                else:
                    self.show_login("login", "Имэйлээ баталгаажуулаад нэвтэрнэ үү")

            def fail(exc):
                btn.config(state="normal")
                err.config(text=friendly(exc))

            self.bg.run(fn, done, fail)

        btn = button(f, "Бүртгүүлэх" if register else "Нэвтрэх", submit, primary=True)
        btn.pack(anchor="w")
        err.pack(anchor="w", pady=(8, 0))
        links = tk.Frame(f, bg=BG)
        links.pack(anchor="w", pady=(14, 0))
        self._link(links, "Нэвтрэх" if register else "Шинээр бүртгүүлэх",
                   lambda: self.show_login("login" if register else "register"))
        if not register:
            self._link(links, "Нууц үг мартсан?", lambda: self.show_forgot(email.get().strip()))
        server = load_server(self.cfg)
        host = server[0].replace("https://", "") if server else ""
        tk.Label(f, text=f"Сервер: {host}", font=(FONT, 10), fg=MUTED, bg=BG).pack(anchor="w", pady=(10, 0))
        self._link(f, "Сервер солих", self._reset_server, small=True)
        for w in (email, pw) + ((pw2,) if register else ()):
            w.bind("<Return>", submit)
        (name or email).focus_set()

    # ------------------------------------------------------------------ нууц үг сэргээх
    def show_forgot(self, prefill: str = "", message: str = "") -> None:
        f = self._frame("Нууц үг сэргээх", "Имэйл рүү тань 6 оронтой код илгээнэ. "
                                           "Имэйл ирэхгүй бол админд хандаж түр нууц үг авна уу.")
        email = self._field(f, "Имэйл")
        email.insert(0, prefill)
        err = tk.Label(f, text=message, font=(FONT, 11), fg=RED, bg=BG, wraplength=320, justify="left")

        def send(_=None):
            e = email.get().strip()
            if "@" not in e:
                return err.config(text="Имэйлээ зөв оруулна уу")
            btn.config(state="disabled")
            self.bg.run(lambda: self.supa.request_recovery(e), lambda _: self.show_reset(e),
                        lambda exc: (btn.config(state="normal"), err.config(text=friendly(exc))))

        btn = button(f, "Код илгээх", send, primary=True)
        btn.pack(anchor="w")
        err.pack(anchor="w", pady=(8, 0))
        self._link(f, "← Нэвтрэх", self.show_login, small=True)
        email.bind("<Return>", send)
        email.focus_set()

    def show_reset(self, email_addr: str) -> None:
        f = self._frame("Шинэ нууц үг", f"{email_addr} хаяг руу илгээсэн кодыг оруулна уу.")
        code = self._field(f, "Код")
        pw = self._field(f, "Шинэ нууц үг", show="•")
        pw2 = self._field(f, "Шинэ нууц үг давтах", show="•")
        err = tk.Label(f, text="", font=(FONT, 11), fg=RED, bg=BG, wraplength=320, justify="left")

        def submit(_=None):
            c, p = code.get().strip(), pw.get()
            if not c or len(p) < 6:
                return err.config(text="Код болон 6+ тэмдэгттэй нууц үгээ оруулна уу")
            if p != pw2.get():
                return err.config(text="Нууц үг таарахгүй байна")
            btn.config(state="disabled")

            def work():
                self.supa.verify_recovery(email_addr, c)
                self.supa.update_password(p)

            self.bg.run(work, lambda _: self.check_profile(),
                        lambda exc: (btn.config(state="normal"), err.config(text=friendly(exc))))

        btn = button(f, "Нууц үг солих", submit, primary=True)
        btn.pack(anchor="w")
        err.pack(anchor="w", pady=(8, 0))
        self._link(f, "Код дахин илгээх", lambda: self.show_forgot(email_addr), small=True)
        for w in (code, pw, pw2):
            w.bind("<Return>", submit)
        code.focus_set()

    def _link(self, parent, text, command, small=False):
        lbl = tk.Label(parent, text=text, font=(FONT, 10 if small else 11, "underline"), fg="#0969da", bg=BG,
                       cursor="hand2")
        lbl.pack(anchor="w")
        lbl.bind("<Button-1>", lambda _: command())
        return lbl

    def _reset_server(self) -> None:
        clear_server()
        self.show_server()

    def check_profile(self) -> None:
        self._busy("Эрх шалгаж байна…")

        def fail(exc):
            if isinstance(exc, NetworkError) and self.supa.cached_profile:
                log.info("Интернэтгүй — хадгалсан профайлаар эхэлнэ")
                self.route(self.supa.cached_profile)
            else:
                self.show_login("login", friendly(exc))

        self.bg.run(self.supa.fetch_profile, self.route, fail)

    def route(self, profile: dict | None) -> None:
        if not profile:
            self.supa.sign_out()
            return self.show_login("login", "Профайл олдсонгүй. Админ supabase/schema.sql-ийг ажиллуулсан эсэхийг "
                                            "шалгана уу.")
        if not profile.get("active", True):
            return self.show_blocked()
        if profile.get("role") == "pending":
            return self.show_pending(profile)
        self.on_ready(self.supa, profile)

    def show_pending(self, profile: dict) -> None:
        f = self._frame("Баталгаажуулалт хүлээж байна",
                        f"{profile.get('full_name') or profile.get('email')}, таны бүртгэл амжилттай үүслээ. "
                        "Админ таны эрх, хэлтсийг оноосны дараа цаг бүртгэл автоматаар эхэлнэ.")
        status = tk.Label(f, text="Эрх: " + ROLE_TEXT["pending"], font=(FONT, 11), fg=MUTED, bg=BG)
        status.pack(anchor="w", pady=(0, 12))
        row = tk.Frame(f, bg=BG)
        row.pack(anchor="w")
        button(row, "Дахин шалгах", self.check_profile, primary=True).pack(side="left")
        button(row, "Гарах", self.logout).pack(side="left", padx=(8, 0))
        self._pending_job = self.root.after(PENDING_RECHECK_MS, self._recheck_quiet)

    def _recheck_quiet(self) -> None:
        self._pending_job = None

        def done(profile):
            if profile and profile.get("role") != "pending":
                self.route(profile)
            else:
                self._pending_job = self.root.after(PENDING_RECHECK_MS, self._recheck_quiet)

        self.bg.run(self.supa.fetch_profile, done,
                    lambda _: setattr(self, "_pending_job", self.root.after(PENDING_RECHECK_MS, self._recheck_quiet)))

    def _cancel_pending(self) -> None:
        if self._pending_job:
            try:
                self.root.after_cancel(self._pending_job)
            except tk.TclError:
                pass
            self._pending_job = None

    def show_blocked(self) -> None:
        f = self._frame("Эрх хаагдсан", "Таны бүртгэлийг админ идэвхгүй болгосон байна. Админтай холбогдоно уу.")
        button(f, "Гарах", self.logout).pack(anchor="w")

    def logout(self) -> None:
        self.bg.run(self.supa.sign_out, lambda _: self.show_login())
