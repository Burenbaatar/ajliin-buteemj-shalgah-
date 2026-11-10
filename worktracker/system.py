"""Системийн туслах функцууд: автоматаар эхлэх, нэг л хувь ажиллуулах, лог."""
from __future__ import annotations

import logging
import logging.handlers
import plistlib
import socket
import subprocess
import sys
from pathlib import Path

from . import APP_NAME
from .config import data_dir, is_frozen

log = logging.getLogger(__name__)

_SINGLE_INSTANCE_PORT = 48213
_instance_socket: socket.socket | None = None


def setup_logging() -> Path:
    path = data_dir() / "app.log"
    handlers: list[logging.Handler] = [
        logging.handlers.RotatingFileHandler(path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    ]
    if sys.stderr is not None and not is_frozen():
        handlers.append(logging.StreamHandler())
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", handlers=handlers
    )
    return path


def ensure_single_instance() -> bool:
    """Програм давхар ажиллаж цагийг 2 дахин тоолохоос сэргийлнэ."""
    global _instance_socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if sys.platform == "win32":
            s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        s.bind(("127.0.0.1", _SINGLE_INSTANCE_PORT))
        s.listen(1)
    except OSError:
        s.close()
        return False
    _instance_socket = s
    return True


def _mac_app_bundle() -> Path | None:
    exe = Path(sys.executable).resolve()
    return exe.parents[2] if exe.parent.name == "MacOS" else None


def set_autostart(enabled: bool) -> None:
    """Компьютер асахад (хэрэглэгч нэвтрэхэд) автоматаар эхлүүлнэ. Зөвхөн .exe/.app үед."""
    if not is_frozen():
        return
    try:
        if sys.platform == "win32":
            import winreg

            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE
            )
            with key:
                if enabled:
                    winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, f'"{sys.executable}"')
                else:
                    try:
                        winreg.DeleteValue(key, APP_NAME)
                    except FileNotFoundError:
                        pass
        elif sys.platform == "darwin":
            plist = Path.home() / "Library" / "LaunchAgents" / f"mn.{APP_NAME.lower()}.plist"
            bundle = _mac_app_bundle()
            if enabled and bundle:
                plist.parent.mkdir(parents=True, exist_ok=True)
                data = {
                    "Label": f"mn.{APP_NAME.lower()}",
                    "ProgramArguments": ["/usr/bin/open", "-a", str(bundle)],
                    "RunAtLoad": True,
                }
                plist.write_bytes(plistlib.dumps(data))
            elif not enabled and plist.exists():
                subprocess.run(["launchctl", "unload", str(plist)], capture_output=True)
                plist.unlink()
    except Exception:  # noqa: BLE001
        log.exception("Автоматаар эхлэх тохиргоо хийж чадсангүй")


def enable_hidpi() -> None:
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:  # noqa: BLE001
            pass
