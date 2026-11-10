"""Tkinter туслах: өнгө, фонт, арын thread-ээс UI шинэчлэх."""
from __future__ import annotations

import queue
import sys
import threading
import tkinter as tk

FONT = "Segoe UI" if sys.platform == "win32" else "Helvetica"
BG = "#ffffff"
SURFACE = "#f6f8fa"
FG = "#1f2328"
MUTED = "#656d76"
BORDER = "#d0d7de"
ACCENT = "#0969da"
GREEN = "#1a7f37"
RED = "#cf222e"
GRAY = "#8c959f"
GAP = "#eaeef2"
PENDING_BG = "#fff8c5"
STATUS_COLOR = {"working": GREEN, "away": RED, "offline": GRAY}


def fmt_dur(sec: float) -> str:
    m = int(round((sec or 0) / 60.0))
    h, m = divmod(m, 60)
    return f"{h}ц {m:02d}м" if h else f"{m} мин"


def fmt_hm(sec: float) -> str:
    m = int(round((sec or 0) / 60.0))
    return f"{m // 60}:{m % 60:02d}"


class Bg:
    """Сүлжээний ажлыг арын thread-д хийж, үр дүнг main thread дээр callback-аар өгнө."""

    def __init__(self, root: tk.Misc):
        self.root = root
        self._q: queue.Queue = queue.Queue()
        self._poll()

    def call_soon(self, fn) -> None:
        """Өөр thread-ээс (tray, socket) main thread дээр fn() дуудуулна."""
        self._q.put((lambda _: fn(), None))

    def run(self, fn, done=None, fail=None) -> None:
        def work():
            try:
                self._q.put((done, fn()))
            except Exception as exc:  # noqa: BLE001
                self._q.put((fail, exc))

        threading.Thread(target=work, daemon=True).start()

    def _poll(self) -> None:
        try:
            while True:
                cb, value = self._q.get_nowait()
                if cb:
                    cb(value)
        except queue.Empty:
            pass
        try:
            self.root.after(80, self._poll)
        except tk.TclError:  # цонх хаагдсан
            pass


def friendly(exc: Exception) -> str:
    return getattr(exc, "friendly", None) or str(exc)


def clear(container: tk.Misc) -> None:
    for w in container.winfo_children():
        if not isinstance(w, tk.Toplevel):
            w.destroy()


def button(parent, text, command, primary=False, **kw) -> tk.Button:
    # macOS дээр tk.Button-ий өнгө өөрчлөгддөггүй тул энгийн товч; Windows дээр өнгөтэй
    opts = {"text": text, "command": command, "padx": 10, "pady": 3, "cursor": "hand2"}
    if primary and sys.platform == "win32":
        opts.update(bg=ACCENT, fg="white", activebackground="#0550ae", activeforeground="white", relief="flat")
    opts.update(kw)
    return tk.Button(parent, **opts)


def bind_wheel(widget: tk.Widget, target: tk.Canvas) -> None:
    """Хулганы дугуйгаар canvas гүйлгэх (Windows / macOS / Linux)."""

    def on_wheel(e):
        if sys.platform == "darwin":
            target.yview_scroll(-int(e.delta), "units")
        else:
            target.yview_scroll(-int(e.delta / 120), "units")

    widget.bind("<Enter>", lambda _: widget.bind_all("<MouseWheel>", on_wheel))
    widget.bind("<Leave>", lambda _: widget.unbind_all("<MouseWheel>"))
    widget.bind("<Button-4>", lambda _: target.yview_scroll(-1, "units"))
    widget.bind("<Button-5>", lambda _: target.yview_scroll(1, "units"))
