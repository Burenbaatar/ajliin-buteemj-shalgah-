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
    # GitHub Releases-ээс автоматаар шинэчлэх (.exe / .app хувилбарт)
    "auto_update": True,
    "update_repo": "",
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
    # Supabase сервер. Ихэвчлэн server.json-оор програмд багцлагдана (README үз).
    "cloud": {
        "supabase_url": "",
        "supabase_key": "",
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


def data_dir() -> Path:
    """Өгөгдлийн сан, тайлан, лог хадгалах хавтас."""
    if not is_frozen():
        d = app_dir() / "data"
    elif sys.platform == "win32":
        d = Path(os.environ.get("APPDATA", Path.home())) / APP_NAME
    elif sys.platform == "darwin":
        d = Path.home() / "Library" / "Application Support" / APP_NAME
    else:
        d = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / APP_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def reports_dir() -> Path:
    d = data_dir() / "reports"
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_server(cfg: dict) -> tuple[str, str] | None:
    """Supabase URL + publishable key.  Дараалал: энэ компьютерт тохируулсан →
    програмд багцалсан server.json → config.json-ийн cloud хэсэг."""
    for path in (data_dir() / "server.json", resource_path("server.json")):
        try:
            d = _read_json(path)
        except (OSError, ValueError):
            continue
        if d.get("supabase_url") and d.get("supabase_key"):
            return str(d["supabase_url"]).strip(), str(d["supabase_key"]).strip()
    c = cfg.get("cloud", {})
    if c.get("supabase_url") and c.get("supabase_key"):
        return str(c["supabase_url"]).strip(), str(c["supabase_key"]).strip()
    return None


def save_server(url: str, key: str) -> None:
    _write_json(data_dir() / "server.json", {"supabase_url": url.strip().rstrip("/"), "supabase_key": key.strip()})


def clear_server() -> None:
    try:
        (data_dir() / "server.json").unlink()
    except FileNotFoundError:
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
