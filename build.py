#!/usr/bin/env python3
"""Програмыг бүтээх скрипт (Windows болон macOS аль алинд ажиллана).

    python build.py              # бүтээнэ
    python build.py --clean      # build/ dist/ .venv-г устгаад цэвэр бүтээнэ
    python build.py --selftest   # бүтээсний дараа --selftest ажиллуулж шалгана
    python build.py --publish    # бүтээгээд админы хавтасны update/ руу байршуулна → ажилтнууд өөрөө шинэчлэгдэнэ

Үр дүн:
    Windows -> dist/AjliinButeemj.exe
    macOS   -> dist/AjliinButeemj-mac/AjliinButeemj.app, dist/AjliinButeemj-mac.zip

Анхаар: .exe-г зөвхөн Windows дээр, .app-г зөвхөн Mac дээр бүтээнэ.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

APP = "AjliinButeemj"
ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
DIST = ROOT / "dist"
IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"


def log(msg: str) -> None:
    print(f"==> {msg}", flush=True)


def run(*cmd: str | Path) -> None:
    args = [str(c) for c in cmd]
    print("   $", " ".join(args), flush=True)
    subprocess.run(args, cwd=ROOT, check=True)


def venv_python() -> Path:
    return VENV / ("Scripts/python.exe" if IS_WIN else "bin/python")


def ensure_venv() -> Path:
    py = venv_python()
    if not py.exists():
        log("Виртуал орчин (.venv) үүсгэж байна")
        run(sys.executable, "-m", "venv", VENV)
    log("Хамаарлуудыг суулгаж байна")
    run(py, "-m", "pip", "install", "--upgrade", "pip")
    run(py, "-m", "pip", "install", "-r", ROOT / "requirements.txt", "pyinstaller")
    return py


def copy_config(dest_dir: Path) -> None:
    # Имэйл/Telegram тохируулсан бол config.json-г програмын хажууд хуулна
    cfg = ROOT / "config.json"
    if cfg.exists():
        shutil.copy2(cfg, dest_dir / "config.json")
        log(f"config.json -> {dest_dir.relative_to(ROOT)}")


def package_windows() -> Path:
    exe = DIST / f"{APP}.exe"
    if not exe.exists():
        sys.exit(f"АЛДАА: {exe} үүссэнгүй")
    copy_config(DIST)
    return exe


def package_mac() -> Path:
    app = DIST / f"{APP}.app"
    if not app.exists():
        sys.exit(f"АЛДАА: {app} үүссэнгүй")
    pkg = DIST / f"{APP}-mac"
    zip_path = DIST / f"{APP}-mac.zip"
    shutil.rmtree(pkg, ignore_errors=True)
    zip_path.unlink(missing_ok=True)
    pkg.mkdir(parents=True)
    # ditto нь .app доторх symlink, гарын үсэг, extended attribute-г зөв хадгална
    run("ditto", app, pkg / app.name)
    copy_config(pkg)
    run("ditto", "-c", "-k", "--keepParent", pkg, zip_path)
    return zip_path


def selftest(target: Path) -> None:
    log("Self-test ажиллуулж байна")
    if IS_MAC:
        binary = DIST / f"{APP}.app" / "Contents" / "MacOS" / APP
    else:
        binary = target
    rc = subprocess.run([str(binary), "--selftest"], cwd=ROOT).returncode
    if IS_WIN:
        app_log = Path(os.environ.get("APPDATA", "")) / APP / "app.log"
        if app_log.exists():
            print(app_log.read_text(encoding="utf-8", errors="replace"))
    if rc != 0:
        sys.exit(f"АЛДАА: self-test амжилтгүй (код {rc})")
    log("Self-test амжилттай")


def app_version() -> str:
    text = (ROOT / "worktracker" / "__init__.py").read_text(encoding="utf-8")
    m = re.search(r'__version__\s*=\s*"([^"]+)"', text)
    return m.group(1) if m else "0"


def share_local_path() -> Path | None:
    """--admin-setup үүсгэсэн хавтас (энэ компьютер дээр)."""
    candidates = [Path(os.environ.get("APPDATA", "")) / APP / "share.json", ROOT / "data" / "share.json"]
    if IS_MAC:
        candidates.insert(0, Path.home() / "Library" / "Application Support" / APP / "share.json")
    for f in candidates:
        try:
            local = json.loads(f.read_text(encoding="utf-8-sig")).get("local_path")
        except (OSError, ValueError):
            continue
        if local and Path(local).is_dir():
            return Path(local)
    return None


def protect_update_dir(folder: Path) -> None:
    """update/ хавтсыг ажилтнуудын тусгай хэрэглэгчид ЗӨВХӨН УНШИГДАХ болгоно — хэн нэгэн хортой exe тавьж
    бүх компьютерт тараахаас хамгаална. Хавтасны эзэмшигч (админ) өөрөө бичих эрхтэй үлдэнэ."""
    user = os.environ.get("USERNAME") or ""
    grants = [f"{user}:(OI)(CI)F"] if user else []
    grants += ["*S-1-5-32-544:(OI)(CI)F", "*S-1-5-18:(OI)(CI)F", "ajliinbuteemj:(OI)(CI)RX"]
    r = subprocess.run(["icacls", str(folder), "/inheritance:r", "/grant:r", *grants],
                       capture_output=True, text=True)
    if r.returncode:
        print(f"АНХААР: update/ хавтсыг хамгаалж чадсангүй: {(r.stdout + r.stderr).strip()[:300]}")
    else:
        log("update/ хавтас: ажилтнууд зөвхөн уншина")


def publish(target: Path) -> None:
    """Бүтээсэн програмыг админы хавтасны update/ руу байршуулна → ажилтнуудын програм өөрөө шинэчлэгдэнэ."""
    local = share_local_path()
    if local is None:
        sys.exit("АЛДАА: админы хавтас олдсонгүй. Энэ компьютер дээр эхлээд `python main.py --admin-setup` хийнэ.")
    folder = local / "update"
    folder.mkdir(exist_ok=True)
    if IS_WIN:
        protect_update_dir(folder)
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    tmp = folder / (target.name + ".tmp")
    shutil.copy2(target, tmp)
    os.replace(tmp, folder / target.name)
    meta = {"version": app_version(), "file": target.name, "size": target.stat().st_size, "sha256": digest,
            "published_at": datetime.now().isoformat(timespec="seconds")}
    # version.json-г хамгийн сүүлд бичнэ — exe бүрэн хуулагдахаас өмнө хэн ч шинэчлэл эхлүүлэхгүй
    (folder / "version.json.tmp").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(folder / "version.json.tmp", folder / "version.json")
    log(f"Байршууллаа: {folder / target.name} (хувилбар {meta['version']})")
    print("   Ажилтнуудын програм 30 минутын дотор (эсвэл дараагийн удаа асахад) өөрөө шинэчлэгдэнэ.")


def ensure_dist_writable() -> None:
    exe = DIST / f"{APP}.exe"
    if IS_WIN and exe.exists():
        try:
            with open(exe, "r+b"):
                pass
        except PermissionError:
            sys.exit(f"АЛДАА: {exe.relative_to(ROOT)} одоо ажиллаж байна. Tray дүрс → \"Програмыг хаах\" дараад дахин "
                     "оролдоно уу. (Зөвлөмж: өөрийн програмыг dist/-ээс биш, тусдаа хавтсанд хуулж ажиллуулаарай.)")


def main() -> None:
    parser = argparse.ArgumentParser(description="AjliinButeemj програмыг бүтээнэ")
    parser.add_argument("--clean", action="store_true", help="build/, dist/, .venv-г устгаад эхнээс нь бүтээнэ")
    parser.add_argument("--selftest", action="store_true", help="бүтээсний дараа --selftest ажиллуулна")
    parser.add_argument("--publish", action="store_true",
                        help="бүтээсний дараа админы хавтасны update/ руу байршуулж, ажилтнуудыг автоматаар шинэчлэх")
    args = parser.parse_args()

    if not (IS_WIN or IS_MAC):
        sys.exit("АЛДАА: зөвхөн Windows эсвэл macOS дээр бүтээнэ")
    if sys.version_info < (3, 9):
        sys.exit("АЛДАА: Python 3.9 эсвэл түүнээс шинэ хувилбар хэрэгтэй")

    if args.clean:
        log("Хуучин build/, dist/, .venv-г устгаж байна")
        for d in (ROOT / "build", DIST, VENV):
            shutil.rmtree(d, ignore_errors=True)

    if (ROOT / "share.json").exists():
        log("share.json (админы хавтас) програмд багцлагдана")
    else:
        print("АНХААР: share.json алга - эхлээд админы компьютер дээр `python main.py --admin-setup` ажиллуулна "
              "(эсвэл ажилтан анх нээхэд хавтасны замыг асууна)")

    ensure_dist_writable()
    py = ensure_venv()
    log(f"PyInstaller-ээр бүтээж байна (хувилбар {app_version()})")
    run(py, "-m", "PyInstaller", ROOT / f"{APP}.spec", "--noconfirm")

    target = package_windows() if IS_WIN else package_mac()
    if args.selftest:
        selftest(target)
    if args.publish:
        publish(target)

    print()
    print(f"Бэлэн: {target.relative_to(ROOT)}")


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as e:
        sys.exit(f"АЛДАА: команд амжилтгүй (код {e.returncode}): {' '.join(map(str, e.cmd))}")
    except KeyboardInterrupt:
        sys.exit("Цуцлагдлаа")
