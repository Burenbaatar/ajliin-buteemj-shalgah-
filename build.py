#!/usr/bin/env python3
"""Програмыг бүтээх скрипт (Windows болон macOS аль алинд ажиллана).

    python build.py              # бүтээнэ
    python build.py --clean      # build/ dist/ .venv-г устгаад цэвэр бүтээнэ
    python build.py --selftest   # бүтээсний дараа --selftest ажиллуулж шалгана

Үр дүн:
    Windows -> dist/AjliinButeemj.exe (+ .sha256)
    macOS   -> dist/AjliinButeemj-mac/AjliinButeemj.app, dist/AjliinButeemj-mac.zip (+ .sha256)

Анхаар: .exe-г зөвхөн Windows дээр, .app-г зөвхөн Mac дээр бүтээнэ.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
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


def sha256_file(path: Path) -> Path:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    out = path.with_name(path.name + ".sha256")
    out.write_text(h.hexdigest(), encoding="ascii")
    return out


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
    sha256_file(exe)
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
    sha256_file(zip_path)
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


def main() -> None:
    parser = argparse.ArgumentParser(description="AjliinButeemj програмыг бүтээнэ")
    parser.add_argument("--clean", action="store_true", help="build/, dist/, .venv-г устгаад эхнээс нь бүтээнэ")
    parser.add_argument("--selftest", action="store_true", help="бүтээсний дараа --selftest ажиллуулна")
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

    py = ensure_venv()
    log("PyInstaller-ээр бүтээж байна")
    run(py, "-m", "PyInstaller", ROOT / f"{APP}.spec", "--noconfirm")

    target = package_windows() if IS_WIN else package_mac()
    if args.selftest:
        selftest(target)

    print()
    print(f"Бэлэн: {target.relative_to(ROOT)}")


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as e:
        sys.exit(f"АЛДАА: команд амжилтгүй (код {e.returncode}): {' '.join(map(str, e.cmd))}")
    except KeyboardInterrupt:
        sys.exit("Цуцлагдлаа")
