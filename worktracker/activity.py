"""Идэвхтэй (дээр нь байгаа) апп, гар/хулганы идэвхгүй хугацаа, дэлгэц түгжигдсэн эсэх.

Windows: pywin32 + psutil
macOS:   pyobjc (AppKit, Quartz)
Linux:   xdotool / xprintidle (байвал)

Зөвхөн аппын НЭРийг авна. Цонхны гарчиг, дэлгэцийн зураг авахгүй.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys

log = logging.getLogger(__name__)

UNKNOWN_APP = "(Тодорхойгүй)"


# ----------------------------------------------------------------------------- Windows
if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    import psutil
    import win32api
    import win32gui
    import win32process

    _user32 = ctypes.windll.user32
    _kernel32 = ctypes.windll.kernel32
    _kernel32.GetTickCount.restype = wintypes.DWORD
    _user32.OpenInputDesktop.restype = wintypes.HANDLE
    _user32.OpenInputDesktop.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _user32.CloseDesktop.argtypes = [wintypes.HANDLE]

    class _LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

    _name_cache: dict[str, str] = {}
    _LOCK_PROCS = {"lockapp.exe", "logonui.exe"}

    def _friendly_name(exe_path: str) -> str:
        """chrome.exe -> 'Google Chrome' (exe файлын FileDescription-оос)."""
        if exe_path in _name_cache:
            return _name_cache[exe_path]
        name = os.path.splitext(os.path.basename(exe_path))[0]
        try:
            lang, codepage = win32api.GetFileVersionInfo(exe_path, "\\VarFileInfo\\Translation")[0]
            key = f"\\StringFileInfo\\{lang:04x}{codepage:04x}\\FileDescription"
            desc = win32api.GetFileVersionInfo(exe_path, key)
            if desc and desc.strip():
                name = desc.strip()
        except Exception:  # noqa: BLE001
            pass
        _name_cache[exe_path] = name
        return name

    def _pid_of(hwnd: int) -> int:
        return win32process.GetWindowThreadProcessId(hwnd)[1]

    def _uwp_real_pid(hwnd: int, host_pid: int) -> int:
        """Calculator, Settings зэрэг UWP аппууд ApplicationFrameHost дотор ажилладаг."""
        found = [host_pid]

        def cb(child, _):
            pid = _pid_of(child)
            if pid != host_pid:
                found[0] = pid
                return False
            return True

        try:
            win32gui.EnumChildWindows(hwnd, cb, None)
        except Exception:  # noqa: BLE001  (cb False буцаахад pywin32 алдаа шиддэг)
            pass
        return found[0]

    def get_active_app() -> str | None:
        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            return None
        try:
            pid = _pid_of(hwnd)
            proc = psutil.Process(pid)
            pname = proc.name().lower()
            if pname in _LOCK_PROCS:
                return None
            if pname == "applicationframehost.exe":
                proc = psutil.Process(_uwp_real_pid(hwnd, pid))
            try:
                return _friendly_name(proc.exe())
            except psutil.AccessDenied:  # админ эрхтэй процесс (Task Manager г.м.)
                return os.path.splitext(proc.name())[0]
        except (psutil.Error, OSError):
            return UNKNOWN_APP

    def get_idle_seconds() -> float | None:
        lii = _LASTINPUTINFO()
        lii.cbSize = ctypes.sizeof(lii)
        if not _user32.GetLastInputInfo(ctypes.byref(lii)):
            return None
        return ((_kernel32.GetTickCount() - lii.dwTime) & 0xFFFFFFFF) / 1000.0

    def is_screen_locked() -> bool:
        # Түгжээтэй үед input desktop нь "Winlogon" болдог тул энгийн процесс нээж чадахгүй
        DESKTOP_SWITCHDESKTOP = 0x0100
        hdesk = _user32.OpenInputDesktop(0, False, DESKTOP_SWITCHDESKTOP)
        if not hdesk:
            return True
        _user32.CloseDesktop(hdesk)
        return False


# ----------------------------------------------------------------------------- macOS
elif sys.platform == "darwin":
    try:
        import Quartz
        from AppKit import NSWorkspace
    except ImportError:  # pyobjc суулгаагүй бол системийн командуудаар
        NSWorkspace = None
        Quartz = None
        log.warning("pyobjc олдсонгүй: lsappinfo/ioreg ашиглана")

    _LOCK_APPS = {"loginwindow", "ScreenSaverEngine"}

    def _lsappinfo_front() -> str | None:
        try:
            asn = subprocess.run(["lsappinfo", "front"], capture_output=True, text=True, timeout=2).stdout.strip()
            out = subprocess.run(
                ["lsappinfo", "info", "-only", "name", asn], capture_output=True, text=True, timeout=2
            ).stdout
            # "LSDisplayName"="Safari"
            return out.split("=", 1)[1].strip().strip('"') if "=" in out else None
        except (OSError, subprocess.SubprocessError, IndexError):
            return None

    def get_active_app() -> str | None:
        name = None
        if NSWorkspace is not None:
            try:
                # activeApplication() нь run loop-гүй thread дээр ч шинэ утга өгдөг
                # (frontmostApplication() хуучирсан утга буцаадаг).
                info = NSWorkspace.sharedWorkspace().activeApplication()
                name = str(info["NSApplicationName"]) if info else None
            except Exception:  # noqa: BLE001
                name = None
        if name is None:
            name = _lsappinfo_front()
        if name in _LOCK_APPS:
            return None
        return name or UNKNOWN_APP

    def get_idle_seconds() -> float | None:
        if Quartz is not None:
            return float(
                Quartz.CGEventSourceSecondsSinceLastEventType(
                    Quartz.kCGEventSourceStateHIDSystemState, Quartz.kCGAnyInputEventType
                )
            )
        try:
            out = subprocess.run(["ioreg", "-c", "IOHIDSystem"], capture_output=True, text=True, timeout=2).stdout
            for line in out.splitlines():
                if "HIDIdleTime" in line:
                    return int(line.split("=")[-1].strip()) / 1e9
        except (OSError, subprocess.SubprocessError, ValueError):
            pass
        return None

    def is_screen_locked() -> bool:
        if Quartz is None:
            return False
        try:
            d = Quartz.CGSessionCopyCurrentDictionary()
            return bool(d and d.get("CGSSessionScreenIsLocked", False))
        except Exception:  # noqa: BLE001
            return False


# ----------------------------------------------------------------------------- Linux (X11)
else:
    import psutil

    def get_active_app() -> str | None:
        try:
            pid = subprocess.run(
                ["xdotool", "getactivewindow", "getwindowpid"], capture_output=True, text=True, timeout=2
            ).stdout.strip()
            return psutil.Process(int(pid)).name() if pid else UNKNOWN_APP
        except (OSError, ValueError, psutil.Error, subprocess.SubprocessError):
            return UNKNOWN_APP

    def get_idle_seconds() -> float | None:
        try:
            out = subprocess.run(["xprintidle"], capture_output=True, text=True, timeout=2).stdout
            return int(out.strip()) / 1000.0
        except (OSError, ValueError, subprocess.SubprocessError):
            return None

    def is_screen_locked() -> bool:
        return False
