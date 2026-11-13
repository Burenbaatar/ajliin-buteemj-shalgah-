"""Тохиргоо болон файлын замууд (Windows / macOS / Linux)."""
from __future__ import annotations

import copy
import json
import logging
import os
import sys
from pathlib import Path

from . import APP_NAME

DEFAULTS: dict = {
    # Ажилтны нэр (тайлан дээр гарна). Хоосон бол анх ажиллахад асууна.
    "employee_name": "",
    # Камерын дугаар (ихэвчлэн 0)
    "camera_index": 0,
    # Камераар хэдэн секунд тутам шалгах
    "camera_check_interval_sec": 1.0,
    # Царай харагдахгүй болоод хэдэн секундийн дараа "Ажлаас гарсан" гэж үзэх
    "away_grace_sec": 20,
    # Гар/хулгана хөдөлж байвал (царай танигдахгүй байсан ч) хүн байна гэж үзэх
    "input_counts_as_presence": True,
    # Сүүлийн хэдэн секундэд гар/хулгана хөдөлсөн бол "хүн байна" гэж үзэх
    "input_presence_window_sec": 5,
    # Камер олдохгүй үед: хэдэн секунд гар/хулгана хөдлөхгүй бол гарсан гэж үзэх
    "no_camera_idle_timeout_sec": 300,
    # Өдрийн тайлан илгээх цаг (24 цагийн формат)
    "report_time": "18:00",
    # Тайланд үүнээс урт "гарсан" хугацааг жагсааж харуулна (минут)
    "min_away_period_min": 1,
    # Компьютер асахад автоматаар эхлэх (зөвхөн .exe / .app хувилбарт)
    "autostart": True,
    # Цонхыг үргэлж бусад цонхны дээр харуулах
    "always_on_top": False,
    # Автоматаар шинэчлэх (.exe / .app): админы хавтасны update/ хавтаснаас (build.py --publish).
    "auto_update": True,
    "email": {
        "enabled": False,
        "smtp_host": "smtp.gmail.com",
        "smtp_port": 465,
        "use_ssl": True,
        "username": "",
        "password": "",
        "from_addr": "",
        "to_addrs": [],
    },
    "telegram": {
        "enabled": False,
        "bot_token": "",
        "chat_id": "",
    },
    # Админы хуваалцсан хавтас руу хуулах давтамж (секунд). Хавтасны зам share.json-д (README үз).
    "cloud": {
        "push_interval_sec": 60,
    },
}

# Админ самбараас өөрчилж болох компанийн тохиргоо (wt_company.settings)
COMPANY_KEYS = ("away_grace_sec", "input_counts_as_presence", "min_away_period_min", "report_time",
                "liveness_enabled", "liveness_static_sec", "camera_blocked_sec")


def is_frozen() -> bool:
    """PyInstaller-ээр багцлагдсан (.exe / .app) эсэх."""
    return bool(getattr(sys, "frozen", False))


def app_dir() -> Path:
    """Програмын (.exe, .app эсвэл main.py) байгаа хавтас."""
    if is_frozen():
        exe = Path(sys.executable).resolve()
        # macOS: X.app/Contents/MacOS/X -> X.app-ийн хажуугийн хавтас
        if sys.platform == "darwin" and exe.parent.name == "MacOS":
            return exe.parents[3]
        return exe.parent
    return Path(__file__).resolve().parent.parent


def resource_path(*parts: str) -> Path:
    """Багцлагдсан нөөц файлын зам (assets/...)."""
    base = Path(getattr(sys, "_MEIPASS", app_dir()))
    return base.joinpath(*parts)


def installed_data_dir() -> Path:
    """.exe / .app хувилбарын өгөгдлийн хавтас (%APPDATA%\\AjliinButeemj г.м.)."""
    if sys.platform == "win32":
        d = Path(os.environ.get("APPDATA", Path.home())) / APP_NAME
    elif sys.platform == "darwin":
        d = Path.home() / "Library" / "Application Support" / APP_NAME
    else:
        d = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / APP_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def data_dir() -> Path:
    """Өгөгдлийн сан, тайлан, лог хадгалах хавтас. Python-оор ажиллуулахад төслийн data/ хавтас."""
    if is_frozen():
        return installed_data_dir()
    d = app_dir() / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _setup_dirs() -> list[Path]:
    """Админы тохиргоог хаана хадгалах вэ: Python-оор --admin-setup хийсэн бол .exe-д ч хүчинтэй байхаар хоёуланд."""
    dirs = [data_dir()]
    if not is_frozen() and installed_data_dir() != dirs[0]:
        dirs.append(installed_data_dir())
    return dirs


def reports_dir() -> Path:
    d = data_dir() / "reports"
    d.mkdir(parents=True, exist_ok=True)
    return d


SHARE_FILE = "share.json"
ADMIN_FILE = "admin.json"
PBKDF2_ROUNDS = 200_000


def load_share() -> dict | None:
    """Админы хуваалцсан хавтас: {"share": "\\\\ADMIN-PC\\AjliinButeemj", "user": ..., "password": ...,
    "local_path": ...}.  Дараалал: энэ компьютерт тохируулсан → .exe-ийн хажууд → програмд багцалсан."""
    found = []
    for path in (data_dir() / SHARE_FILE, app_dir() / SHARE_FILE, resource_path(SHARE_FILE)):
        try:
            d = _read_json(path)
        except (OSError, ValueError):
            continue
        if d.get("share") or d.get("local_path"):
            found.append(d)
    if not found:
        return None
    # Ажилтан хаягийг гараар бичсэн бол нэвтрэх нэр/нууц үгийг .exe-ийн хажууд/дотор байгаа share.json-оос нөхнө
    info = dict(found[0])
    for other in found[1:]:
        if str(other.get("share", "")).lower() == str(info.get("share", "")).lower():
            for k, v in other.items():
                info.setdefault(k, v)
    return info


def save_share(info: dict) -> None:
    for d in _setup_dirs():
        _write_json(d / SHARE_FILE, info)


# ---------------------------------------------------------------------------- админ (зөвхөн админы компьютерт)
def hash_password(password: str, salt: str | None = None) -> str:
    import hashlib
    import secrets

    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), PBKDF2_ROUNDS)
    return f"pbkdf2${PBKDF2_ROUNDS}${salt}${digest.hex()}"


def check_password(password: str, stored: str) -> bool:
    import hashlib
    import hmac

    try:
        _, rounds, salt, digest = stored.split("$")
        calc = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), int(rounds))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(calc.hex(), digest)


def load_admin() -> dict | None:
    """Энэ компьютер админых бол {"password": hash, "telegram_bot_token": ...}, үгүй бол None."""
    try:
        d = _read_json(data_dir() / ADMIN_FILE)
    except (OSError, ValueError):
        return None
    return d if d.get("password") else None


def save_admin(d: dict) -> None:
    for folder in _setup_dirs():
        path = folder / ADMIN_FILE
        _write_json(path, d)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass


def user_data_dir(user_id: str) -> Path:
    """Хэрэглэгч бүрийн өгөгдөл тусдаа (нэг компьютерийг хэд хэдэн хүн ашиглаж болно)."""
    d = data_dir() / "users" / user_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


# Ажилтан өөрөө оруулдаг, менежерийн тохиргоогоор дарагдахгүй түлхүүрүүд
USER_KEYS = ("employee_name",)


def _read_json(path: Path) -> dict:
    with path.open(encoding="utf-8-sig") as f:  # Notepad-ийн BOM-той файл ч уншина
        return json.load(f)


def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


class Config(dict):
    """dict + хадгалах зам.

    path        — ажилтны тохиргоо (өгөгдлийн хавтаст, үргэлж бичих боломжтой)
    manager_path — менежерийн config.json (байвал), эсвэл None
    """

    path: Path
    manager_path: Path | None = None

    def save(self) -> None:
        if self.manager_path:
            # Менежерийн тохиргоо байгаа үед зөвхөн ажилтны түлхүүрүүдийг хадгална
            data = _read_json(self.path) if self.path.is_file() else {}
            data.update({k: self[k] for k in USER_KEYS})
        else:
            data = dict(self)
        _write_json(self.path, data)


def load_config() -> Config:
    """Тохиргоог 3 давхаргаар уншина:  анхны утга < ажилтны файл < менежерийн файл.

    Менежерийн config.json:  .exe/.app-ийн хажууд тавина. Анх олдохдоо өгөгдлийн
    хавтаст managed.json нэрээр хуулагдана — дараа нь .exe-г зөөсөн, эсвэл macOS
    App Translocation хийсэн ч тохиргоо алдагдахгүй.
    Ажилтны нэр (USER_KEYS) нь ажилтны файлаас авагдана.
    """
    beside = app_dir() / "config.json"
    managed_copy = data_dir() / "managed.json"
    if sys.platform == "darwin" and "/AppTranslocation/" in str(beside):
        logging.getLogger(__name__).warning(
            "macOS App Translocation: хажуудах config.json харагдахгүй. "
            "Шийдэл: xattr -dr com.apple.quarantine <хавтас>"
        )
    user_path = data_dir() / "config.json"

    manager_path: Path | None = None
    manager: dict = {}
    if beside.is_file():
        manager = _read_json(beside)
        manager_path = beside
        try:
            _write_json(managed_copy, manager)
        except OSError:
            pass
    elif managed_copy.is_file():
        manager = _read_json(managed_copy)
        manager_path = managed_copy

    user = _read_json(user_path) if user_path.is_file() else {}

    cfg = Config(_deep_merge(_deep_merge(DEFAULTS, user), manager))
    for k in USER_KEYS:
        if user.get(k):
            cfg[k] = user[k]
    cfg.path = user_path
    cfg.manager_path = manager_path
    if not user_path.is_file():
        cfg.save()  # менежерийн файлгүй бол засахад бэлэн бүрэн файл үүснэ
    return cfg
