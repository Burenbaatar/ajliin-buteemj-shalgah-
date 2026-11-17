"""Идэвхтэй (дээр нь байгаа) апп, гар/хулганы идэвхгүй хугацаа, дэлгэц түгжигдсэн эсэх (Windows: pywin32 + psutil).

Зөвхөн аппын НЭРийг авна. Дэлгэцийн зураг авахгүй. Браузерын хувьд табын гарчгаас зөвхөн
САЙТЫН нэрийг ("Chrome · YouTube") авна — бүтэн гарчиг (видео, имэйлийн нэр г.м.) хадгалагдахгүй.
"""
from __future__ import annotations

import ctypes
import json
import logging
import os
import re
import time
from ctypes import wintypes
from pathlib import Path

import psutil
import win32api
import win32gui
import win32process

log = logging.getLogger(__name__)

UNKNOWN_APP = "(Тодорхойгүй)"
SITE_MAX_LEN = 40

# ----------------------------------------------------------------------------- табын гарчиг → сайтын нэр
_SEP = re.compile(r"\s+[-–—|·•]\s+")
_ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍⁠﻿"))
_BROWSER_WORDS = frozenset({"google chrome", "microsoft edge", "mozilla firefox", "firefox", "brave", "opera",
                            "vivaldi", "inprivate", "incognito", "private browsing"})
_COUNTER = re.compile(r"^\(\d+\+?\)\s*|\s*\(\d+\+?\)$")  # "(23) YouTube" — уншаагүй мэдэгдлийн тоо
_EDGE_MORE = re.compile(r"\s+(?:and|и|ба|болон)\s+(?:\S+\s+){0,2}\d+(?:\s+\S+){1,2}$", re.IGNORECASE)
_ID_LIKE = re.compile(r"@|^[a-z]{3,4}-[a-z]{3,4}-[a-z]{3,4}$|^[\d\s#:-]+$")  # имэйл, Meet-ийн код, дугаар
_URL = re.compile(r"^(?:https?://)?((?:[\w-]+\.)+[a-z]{2,})(?:[/:?#].*)?$", re.IGNORECASE)


def _is_browser_word(seg: str) -> bool:
    low = seg.strip("[]() ").lower()
    return low in _BROWSER_WORDS or low.startswith("mozilla firefox ")  # "... Private Browsing"


def site_from_title(title: str, drop: set[str] | frozenset[str] = frozenset(), edge: bool = False) -> str | None:
    """'(23) Видео нэр - YouTube - Brave' -> 'YouTube'. Ихэнх сайт нэрээ гарчгийн төгсгөлд бичдэг.

    drop — хасах профайлын нэрс (Edge: '... - Profile 1 - Microsoft Edge'). Сайтын нэр олдохгүй бол None.
    """
    parts = [p.strip() for p in _SEP.split((title or "").translate(_ZERO_WIDTH).strip()) if p.strip()]
    while parts and (_is_browser_word(parts[-1]) or parts[-1].lower() in drop):
        parts.pop()
    if not parts:
        return None
    if edge:  # "YouTube and 3 more pages"
        parts[-1] = _EDGE_MORE.sub("", parts[-1]) or parts[-1]
    site = parts[-1]
    if len(parts) > 1 and _ID_LIKE.search(site):
        site = parts[0]
    site = _COUNTER.sub("", site).strip()
    m = _URL.match(site)
    if m:
        site = m.group(1).lower().removeprefix("www.")
    if len(site) > SITE_MAX_LEN:  # үгийн дундуур таслахгүй
        cut = site[:SITE_MAX_LEN]
        site = cut.rsplit(" ", 1)[0] if " " in cut else cut
    return site.strip() or None


# ----------------------------------------------------------------------------- Windows API
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
# exe -> (харуулах нэр, профайлын жагсаалттай "User Data" хавтас %LOCALAPPDATA%-аас)
_BROWSERS = {
    "chrome.exe": ("Chrome", r"Google\Chrome\User Data"),
    "msedge.exe": ("Edge", r"Microsoft\Edge\User Data"),
    "brave.exe": ("Brave", r"BraveSoftware\Brave-Browser\User Data"),
    "vivaldi.exe": ("Vivaldi", r"Vivaldi\User Data"),
    "opera.exe": ("Opera", None),
    "firefox.exe": ("Firefox", None),
}
_PROFILE_TTL_SEC = 600
_profile_cache: dict[str, tuple[float, frozenset[str]]] = {}


def _profile_names(user_data: str | None) -> frozenset[str]:
    """Chromium браузерын профайлын нэрс ("Local State" файлаас) — гарчгаас хасахад."""
    if not user_data:
        return frozenset()
    now = time.monotonic()
    hit = _profile_cache.get(user_data)
    if hit and now - hit[0] < _PROFILE_TTL_SEC:
        return hit[1]
    names: frozenset[str] = frozenset()
    try:
        path = Path(os.environ.get("LOCALAPPDATA", "")) / user_data / "Local State"
        info = json.loads(path.read_text(encoding="utf-8")).get("profile", {}).get("info_cache", {})
        names = frozenset(str(v["name"]).strip().lower() for v in info.values() if v.get("name"))
    except (OSError, ValueError, AttributeError, TypeError):
        pass
    _profile_cache[user_data] = (now, names)
    return names


def _browser_app(hwnd: int, exe: str) -> str:
    label, user_data = _BROWSERS[exe]
    try:
        title = win32gui.GetWindowText(hwnd)
    except Exception:  # noqa: BLE001
        return label
    site = site_from_title(title, _profile_names(user_data), edge=exe == "msedge.exe")
    return f"{label} · {site}" if site else label


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
        if pname in _BROWSERS:
            return _browser_app(hwnd, pname)
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
