"""Админы компьютерийг нэг удаа тохируулна:  AjliinButeemj.exe --admin-setup  (эсвэл python main.py --admin-setup)

1. Админ нууц үг (самбар нээхэд) → data_dir()/admin.json
2. C:\\AjliinButeemjData хавтас → \\\\<энэ компьютер>\\AjliinButeemj нэрээр хуваалцана
3. Ажилтны програмууд холбогдох тусгай хэрэглэгч (ajliinbuteemj) + Windows firewall (File sharing, зөвхөн Private)
4. share.json → энэ компьютерт + програмын хажууд (build.py үүнийг .exe-д багцална)

2–3-р алхам Windows-ийн админ эрх шаардана — програм UAC-аар зөвшөөрөл асууна.
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import socket
import string
import subprocess
import sys
import tempfile
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, simpledialog

from . import APP_TITLE
from .config import app_dir, hash_password, is_frozen, load_admin, save_admin, save_share
from .share import write_json

log = logging.getLogger(__name__)

SHARE_NAME = "AjliinButeemj"
ACCOUNT = "ajliinbuteemj"
DEFAULT_FOLDER = Path(r"C:\AjliinButeemjData") if sys.platform == "win32" else Path.home() / "AjliinButeemjData"
FILE_SHARING_GROUP = "@FirewallAPI.dll,-28502"  # "File and Printer Sharing" — хэлнээс үл хамаарах нэр
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _run(cmd: list[str]) -> tuple[int, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=60, creationflags=NO_WINDOW)
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, str(exc)
    out = (r.stdout or b"") + (r.stderr or b"")
    for enc in ("utf-8", "cp866", "cp1251", "latin-1"):
        try:
            return r.returncode, out.decode(enc).strip()
        except UnicodeDecodeError:
            continue
    return r.returncode, ""


def _powershell(script: str) -> tuple[int, str]:
    return _run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script])


def _account_password() -> str:
    """14 тэмдэгт (net user 14-өөс урт нууц үгэнд Y/N асуудаг), том/жижиг үсэг + тоо."""
    alphabet = string.ascii_letters + string.digits
    while True:
        pw = "".join(secrets.choice(alphabet) for _ in range(14))
        if any(c.islower() for c in pw) and any(c.isupper() for c in pw) and any(c.isdigit() for c in pw):
            return pw


def is_elevated() -> bool:
    if sys.platform != "win32":
        return os.geteuid() == 0 if hasattr(os, "geteuid") else False
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------------------- админ эрхтэй хэсэг
def system_setup(folder: Path, password: str) -> list[str]:
    """Windows: хэрэглэгч, хуваалцах, хандах эрх, firewall. Алдааны жагсаалт буцаана (хоосон = амжилттай)."""
    errors = []
    code, out = _run(["net", "user", ACCOUNT, password, "/add", "/passwordchg:no", "/expires:never",
                      f"/comment:{APP_TITLE} - ажилтны програм хавтсанд холбогдох"])
    if code:
        code, out = _run(["net", "user", ACCOUNT, password])  # аль хэдийн байгаа → нууц үгийг шинэчилнэ
        if code:
            errors.append(f"Хэрэглэгч үүсгэж чадсангүй: {out[:200]}")
    _powershell(f"Set-LocalUser -Name '{ACCOUNT}' -PasswordNeverExpires $true")
    try:  # нэвтрэх дэлгэц дээр харагдуулахгүй
        import winreg

        key = winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE,
                                 r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon\SpecialAccounts\UserList",
                                 0, winreg.KEY_SET_VALUE)
        with key:
            winreg.SetValueEx(key, ACCOUNT, 0, winreg.REG_DWORD, 0)
    except OSError as exc:
        log.info("Хэрэглэгчийг нууж чадсангүй: %s", exc)

    _run(["net", "share", SHARE_NAME, "/delete", "/y"])
    code, out = _run(["net", "share", f"{SHARE_NAME}={folder}", f"/grant:{ACCOUNT},CHANGE",
                      "/grant:Administrators,FULL", f"/remark:{APP_TITLE}"])
    if code:
        errors.append(f"Хавтас хуваалцаж чадсангүй: {out[:200]}")
    code, out = _run(["icacls", str(folder), "/grant", f"{ACCOUNT}:(OI)(CI)M", "/T", "/C", "/Q"])
    if code:
        errors.append(f"Хавтасны эрх тохируулж чадсангүй: {out[:200]}")
    code, out = _powershell(f"Get-NetFirewallRule -Group '{FILE_SHARING_GROUP}' | "
                            "Where-Object { $_.Profile -match 'Private|Domain' } | Enable-NetFirewallRule")
    if code:
        errors.append(f"Firewall тохируулж чадсангүй (File and Printer Sharing-ийг гараар асаана уу): {out[:200]}")
    return errors


def run_system_step(job_file: str) -> int:
    """UAC-аар дахин эхэлсэн (админ эрхтэй) процесс: system_setup хийгээд үр дүнг файлд бичнэ."""
    job = json.loads(Path(job_file).read_text(encoding="utf-8"))
    errors = system_setup(Path(job["folder"]), job["password"])
    job.pop("password", None)
    job["errors"] = errors
    job["done"] = True
    Path(job_file).write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
    return 0 if not errors else 1


def _elevated_system_setup(folder: Path, password: str) -> list[str]:
    if is_elevated():
        return system_setup(folder, password)
    fd, job_file = tempfile.mkstemp(prefix="wt-setup-", suffix=".json")
    os.close(fd)
    Path(job_file).write_text(json.dumps({"folder": str(folder), "password": password}), encoding="utf-8")
    try:
        if is_frozen():
            exe, args = sys.executable, f"--admin-setup-system \"{job_file}\""
        else:
            main_py = Path(__file__).resolve().parent.parent / "main.py"
            exe, args = sys.executable, f"\"{main_py}\" --admin-setup-system \"{job_file}\""
        ps_args = args.replace("'", "''")
        _powershell(f"Start-Process -FilePath '{exe}' -ArgumentList '{ps_args}' -Verb RunAs -Wait -WindowStyle Hidden")
        result = json.loads(Path(job_file).read_text(encoding="utf-8"))
        if not result.get("done"):
            return ["Админ эрх олгогдсонгүй (UAC цонхонд \"Yes\" дарна уу)"]
        return result.get("errors", [])
    finally:
        try:
            os.remove(job_file)
        except OSError:
            pass


# ---------------------------------------------------------------------------- үндсэн урсгал
def _ask_password(root: tk.Tk) -> str | None:
    while True:
        pw = simpledialog.askstring(APP_TITLE, "Админ нууц үг (самбар нээхэд асууна, 6+ тэмдэгт):", show="•",
                                    parent=root)
        if pw is None:
            return None
        if len(pw) < 6:
            messagebox.showwarning(APP_TITLE, "Хамгийн багадаа 6 тэмдэгт", parent=root)
            continue
        if simpledialog.askstring(APP_TITLE, "Нууц үгээ давтана уу:", show="•", parent=root) != pw:
            messagebox.showwarning(APP_TITLE, "Нууц үг таарахгүй байна", parent=root)
            continue
        return pw


def run_admin_setup() -> int:
    root = tk.Tk()
    root.withdraw()
    host = socket.gethostname()
    if not messagebox.askokcancel(
            APP_TITLE,
            f"Энэ компьютерийг ({host}) АДМИНЫ компьютер болгох уу?\n\n"
            f"• {DEFAULT_FOLDER} хавтас үүсгэж, \\\\{host}\\{SHARE_NAME} нэрээр хуваалцана\n"
            "• Ажилтнуудын програм тийшээ өгөгдлөө (JSON) хуулна\n"
            "• Самбарыг зөвхөн энэ компьютерээс, админ нууц үгээр нээнэ\n\n"
            "Энэ компьютер ажлын цагаар асаалттай байх ёстой. Windows админ эрх асууна.", parent=root):
        root.destroy()
        return 1
    pw = _ask_password(root)
    if pw is None:
        root.destroy()
        return 1

    folder = DEFAULT_FOLDER
    (folder / "people").mkdir(parents=True, exist_ok=True)
    if not (folder / "company.json").exists():
        write_json(folder / "company.json", {"settings": {}, "departments": [], "people": {}, "next_dept_id": 1})

    account_pw = _account_password()
    errors = _elevated_system_setup(folder, account_pw) if sys.platform == "win32" else \
        ["Хавтсыг гараар хуваалцана уу (System Settings → General → Sharing → File Sharing)"]

    info = {"share": f"\\\\{host}\\{SHARE_NAME}", "local_path": str(folder),
            "user": f"{host}\\{ACCOUNT}", "password": account_pw}
    save_share(info)
    # Ажилтнуудад тараах .exe-д багцлагдана (local_path-ийг ажилтан ашиглахгүй — тэр компьютерт байхгүй)
    beside = app_dir() / "share.json"
    try:
        write_json(beside, {k: v for k, v in info.items() if k != "local_path"})
    except OSError as exc:
        errors.append(f"{beside} бичиж чадсангүй: {exc}")
    admin = load_admin() or {}
    admin["password"] = hash_password(pw)
    save_admin(admin)

    if errors:
        messagebox.showwarning(APP_TITLE, "Админ нууц үг хадгалагдлаа, гэхдээ:\n\n• " + "\n• ".join(errors) +
                               "\n\nАлдааг засаад --admin-setup-ийг дахин ажиллуулж болно.", parent=root)
    else:
        messagebox.showinfo(APP_TITLE, f"Бэлэн боллоо!\n\nХуваалцсан хавтас: \\\\{host}\\{SHARE_NAME}\n"
                                       f"Тохиргоо: {beside}\n\n"
                                       "Дараагийн алхам: python build.py → dist\\AjliinButeemj.exe-г ажилтнуудад "
                                       "тараана (share.json автоматаар багцлагдана).\n"
                                       "Эсвэл одоогийн .exe-ийн хамт share.json-г хуулж өгнө.", parent=root)
    root.destroy()
    return 0
