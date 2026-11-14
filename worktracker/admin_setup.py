"""Админы компьютерийг тохируулна:  AjliinButeemj.exe --admin-setup  (эсвэл python main.py --admin-setup)

1. Админ нууц үг (самбар нээхэд) → data_dir()/admin.json
2. C:\\AjliinButeemjData — өгөгдөл. ХУВААЛЦАХГҮЙ: ажилтнууд энд шууд хандахгүй, зөвхөн серверээр (hub.py) илгээнэ.
3. Сервер: Windows firewall-д TCP 47815 (зөвхөн Private сүлжээ).
4. hub.json (компьютерийн нэр + порт, нууц зүйлгүй) → build.py үүнийг .exe-д багцална.
5. Хуучин (3.1.x) хувилбартай ажилтнууд шинэчлэлээ авахын тулд C:\\AjliinButeemjShare хавтсыг
   \\\\<компьютер>\\AjliinButeemj нэрээр ЗӨВХӨН УНШИГДАХ хуваалцана (дотор нь зөвхөн update\\). Бүгд 3.2+ болмогц хэрэггүй.

2, 3, 5-р алхам Windows-ийн админ эрх шаардана — програм UAC-аар зөвшөөрөл асууна. Дахин ажиллуулахад өгөгдөл устахгүй.
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
from .config import (HUB_FILE, HUB_PORT, SHARE_FILE, app_dir, hash_password, is_frozen, load_admin, load_share,
                     save_admin, save_hub, save_share)
from .share import write_json

log = logging.getLogger(__name__)

SHARE_NAME = "AjliinButeemj"
ACCOUNT = "ajliinbuteemj"
DEFAULT_FOLDER = Path(r"C:\AjliinButeemjData") if sys.platform == "win32" else Path.home() / "AjliinButeemjData"
LEGACY_FOLDER = Path(r"C:\AjliinButeemjShare") if sys.platform == "win32" else Path.home() / "AjliinButeemjShare"
FILE_SHARING_GROUP = "@FirewallAPI.dll,-28502"  # "File and Printer Sharing" — хэлнээс үл хамаарах нэр
FIREWALL_RULE = "AjliinButeemj server"
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
def system_setup(data_folder: Path, legacy_folder: Path, password: str, port: int) -> list[str]:
    """Windows: firewall (сервер), өгөгдлийн хавтсыг хаах, хуучин хувилбарт зөвхөн уншигдах шинэчлэлийн хавтас."""
    errors = []
    # --- сервер
    _run(["netsh", "advfirewall", "firewall", "delete", "rule", f"name={FIREWALL_RULE}"])
    code, out = _run(["netsh", "advfirewall", "firewall", "add", "rule", f"name={FIREWALL_RULE}", "dir=in",
                      "action=allow", "protocol=TCP", f"localport={port}", "profile=private,domain"])
    if code:
        errors.append(f"Firewall-д серверийн порт ({port}) нээж чадсангүй: {out[:200]}")

    # --- өгөгдлийн хавтсанд тусгай хэрэглэгчийн хандалтыг хасна (өмнө нь бичих эрхтэй байсан)
    _run(["icacls", str(data_folder), "/remove:g", ACCOUNT, "/T", "/C", "/Q"])

    # --- хуучин хувилбарт: зөвхөн уншигдах шинэчлэлийн хавтас
    code, out = _run(["net", "user", ACCOUNT, password, "/add", "/passwordchg:no", "/expires:never",
                      f"/comment:{APP_TITLE} - хуучин хувилбарын шинэчлэл"])
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
    code, out = _run(["net", "share", f"{SHARE_NAME}={legacy_folder}", f"/grant:{ACCOUNT},READ",
                      "/grant:Administrators,FULL", f"/remark:{APP_TITLE} (хуучин хувилбарын шинэчлэл)"])
    if code:
        errors.append(f"Шинэчлэлийн хавтас хуваалцаж чадсангүй: {out[:200]}")
    _run(["icacls", str(legacy_folder), "/grant", f"{ACCOUNT}:(OI)(CI)RX", "/T", "/C", "/Q"])
    code, out = _powershell(f"Get-NetFirewallRule -Group '{FILE_SHARING_GROUP}' | "
                            "Where-Object { $_.Profile -match 'Private|Domain' } | Enable-NetFirewallRule")
    if code:
        errors.append(f"Firewall (File and Printer Sharing) тохируулж чадсангүй: {out[:200]}")
    return errors


def run_system_step(job_file: str) -> int:
    """UAC-аар дахин эхэлсэн (админ эрхтэй) процесс: system_setup хийгээд үр дүнг файлд бичнэ."""
    job = json.loads(Path(job_file).read_text(encoding="utf-8"))
    errors = system_setup(Path(job["folder"]), Path(job["legacy"]), job["password"], int(job["port"]))
    job.pop("password", None)
    job["errors"] = errors
    job["done"] = True
    Path(job_file).write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
    return 0 if not errors else 1


def _elevated_system_setup(folder: Path, legacy: Path, password: str, port: int) -> list[str]:
    if is_elevated():
        return system_setup(folder, legacy, password, port)
    fd, job_file = tempfile.mkstemp(prefix="wt-setup-", suffix=".json")
    os.close(fd)
    Path(job_file).write_text(json.dumps({"folder": str(folder), "legacy": str(legacy), "password": password,
                                          "port": port}), encoding="utf-8")
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


def prepare_folders(data: Path, legacy: Path) -> None:
    """Өгөгдлийн хавтас + хуучин хувилбарт зориулсан шинэчлэлийн хавтас (company.json-ийн хоосон хуулбартай —
    3.1.x програм хавтас "байна" гэж үзэж шинэчлэлээ шалгана)."""
    (data / "people").mkdir(parents=True, exist_ok=True)
    (data / "update").mkdir(parents=True, exist_ok=True)
    if not (data / "company.json").exists():
        write_json(data / "company.json", {"settings": {}, "departments": [], "people": {}, "next_dept_id": 1})
    (legacy / "update").mkdir(parents=True, exist_ok=True)
    write_json(legacy / "company.json", {"settings": {}, "departments": [], "people": {}, "removed": {},
                                         "next_dept_id": 1})


def run_admin_setup() -> int:
    root = tk.Tk()
    root.withdraw()
    host = socket.gethostname()
    if not messagebox.askokcancel(
            APP_TITLE,
            f"Энэ компьютерийг ({host}) АДМИНЫ компьютер болгох уу?\n\n"
            f"• Өгөгдөл {DEFAULT_FOLDER}-д хадгалагдана (бусдад хуваалцахгүй)\n"
            f"• Ажилтнуудын програм энэ компьютерийн сервер рүү (TCP {HUB_PORT}) өгөгдлөө илгээнэ —\n"
            "  хүн бүр зөвхөн өөрийнхөө өгөгдлийг\n"
            "• Самбарыг зөвхөн энэ компьютерээс, админ нууц үгээр нээнэ\n\n"
            "Энэ компьютер ажлын цагаар асаалттай байх ёстой. Windows админ эрх асууна.", parent=root):
        root.destroy()
        return 1
    pw = _ask_password(root)
    if pw is None:
        root.destroy()
        return 1

    prepare_folders(DEFAULT_FOLDER, LEGACY_FOLDER)
    old = load_share() or {}
    account_pw = old.get("password") or _account_password()  # хуучин програмууд хуучин нууц үгээ хэрэглэсээр
    errors = _elevated_system_setup(DEFAULT_FOLDER, LEGACY_FOLDER, account_pw, HUB_PORT) \
        if sys.platform == "win32" else ["Firewall-д TCP 47815-ыг гараар нээнэ үү"]

    save_share({"local_path": str(DEFAULT_FOLDER), "share": f"\\\\{host}\\{SHARE_NAME}",
                "user": f"{host}\\{ACCOUNT}", "password": account_pw})
    hub = {"host": host, "port": HUB_PORT}
    save_hub(hub)
    # Ажилтнуудад тараах .exe-д багцлагдана (нууц зүйлгүй). Хуучин share.json (нууц үгтэй) хэрэггүй болсон.
    beside = app_dir() / HUB_FILE
    try:
        write_json(beside, hub)
    except OSError as exc:
        errors.append(f"{beside} бичиж чадсангүй: {exc}")
    try:
        (app_dir() / SHARE_FILE).unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        log.info("Хуучин share.json устгаж чадсангүй: %s", exc)
    admin = load_admin() or {}
    admin["password"] = hash_password(pw)
    save_admin(admin)

    if errors:
        messagebox.showwarning(APP_TITLE, "Админ нууц үг хадгалагдлаа, гэхдээ:\n\n• " + "\n• ".join(errors) +
                               "\n\nАлдааг засаад --admin-setup-ийг дахин ажиллуулж болно.", parent=root)
    else:
        messagebox.showinfo(APP_TITLE, f"Бэлэн боллоо!\n\nСервер: {host}:{HUB_PORT}\nӨгөгдөл: {DEFAULT_FOLDER}\n"
                                       f"Тохиргоо: {beside}\n\n"
                                       "Дараагийн алхам: python build.py --publish → бүх ажилтан шинэ хувилбарыг "
                                       "өөрөө авна (hub.json автоматаар багцлагдана).", parent=root)
    root.destroy()
    return 0
