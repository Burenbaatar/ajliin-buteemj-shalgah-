#!/usr/bin/env python3
"""Програмыг бүтээх скрипт (зөвхөн Windows).

    python build.py              # бүтээнэ
    python build.py --clean      # build/ dist/ .venv-г устгаад цэвэр бүтээнэ
    python build.py --selftest   # бүтээсний дараа --selftest ажиллуулж шалгана
    python build.py --publish    # бүтээгээд админы хавтасны update/ руу байршуулна → ажилтнууд өөрөө шинэчлэгдэнэ

Үр дүн: dist/AjliinButeemj.exe
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


def log(msg: str) -> None:
    print(f"==> {msg}", flush=True)


def run(*cmd: str | Path) -> None:
    args = [str(c) for c in cmd]
    print("   $", " ".join(args), flush=True)
    subprocess.run(args, cwd=ROOT, check=True)


def venv_python() -> Path:
    return VENV / "Scripts" / "python.exe"


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


def selftest(target: Path) -> None:
    log("Self-test ажиллуулж байна")
    rc = subprocess.run([str(target), "--selftest"], cwd=ROOT).returncode
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


def refresh_hub_ips() -> None:
    """hub.json-д энэ (админы) компьютерийн одоогийн дотоод IP-г нэмнэ → exe-д багцлагдана. Сүлжээний DNS
    компьютерийн нэрийг мэддэггүй үед ажилтны програм IP-ээр холбогдоно (client.HubClient)."""
    import socket

    from worktracker.net import local_ips

    path = ROOT / "hub.json"
    try:
        hub = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return
    if str(hub.get("host", "")).lower() != socket.gethostname().lower():
        return  # өөр компьютерийн hub.json — тэр компьютерийн IP-г эндээс мэдэхгүй
    ips = local_ips()
    if ips and ips != hub.get("ips"):
        hub["ips"] = ips
        path.write_text(json.dumps(hub, ensure_ascii=False), encoding="utf-8")
    log(f"hub.json IP: {', '.join(hub.get('ips') or []) or 'олдсонгүй'} (нэрээр олдохгүй үед ажилтнууд үүгээр "
        "холбогдоно)")


def share_local_path() -> Path | None:
    """--admin-setup үүсгэсэн хавтас (энэ компьютер дээр)."""
    candidates = [Path(os.environ.get("APPDATA", "")) / APP / "share.json", ROOT / "data" / "share.json"]
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


LEGACY_SHARE = Path(r"C:\AjliinButeemjShare")  # 3.1.x хувилбарууд шинэчлэлээ эндээс (хуваалцсан, зөвхөн унших) авна


def _publish_to(folder: Path, target: Path, meta: dict) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    tmp = folder / (target.name + ".tmp")
    shutil.copy2(target, tmp)
    os.replace(tmp, folder / target.name)
    # version.json-г хамгийн сүүлд бичнэ — exe бүрэн хуулагдахаас өмнө хэн ч шинэчлэл эхлүүлэхгүй
    (folder / "version.json.tmp").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(folder / "version.json.tmp", folder / "version.json")
    log(f"Байршууллаа: {folder / target.name}")


def publish(target: Path) -> None:
    """Бүтээсэн програмыг админы компьютерийн update/ руу байршуулна → ажилтнуудын програм өөрөө шинэчлэгдэнэ
    (3.2+: серверээр; 3.1.x: хуучин хуваалцсан хавтаснаас)."""
    local = share_local_path()
    if local is None:
        sys.exit("АЛДАА: админы хавтас олдсонгүй. Энэ компьютер дээр эхлээд `python main.py --admin-setup` хийнэ.")
    meta = {"version": app_version(), "file": target.name, "size": target.stat().st_size,
            "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            "published_at": datetime.now().isoformat(timespec="seconds")}
    _publish_to(local / "update", target, meta)
    if LEGACY_SHARE.is_dir():
        _publish_to(LEGACY_SHARE / "update", target, meta)
        protect_update_dir(LEGACY_SHARE / "update")
    print(f"   Хувилбар {meta['version']} — ажилтнуудын програм 30 минутын дотор (эсвэл дараагийн удаа асахад) "
          "өөрөө шинэчлэгдэнэ.")


def ensure_dist_writable() -> None:
    exe = DIST / f"{APP}.exe"
    if exe.exists():
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

    if sys.platform != "win32":
        sys.exit("АЛДАА: зөвхөн Windows дээр бүтээнэ")
    if sys.version_info < (3, 9):
        sys.exit("АЛДАА: Python 3.9 эсвэл түүнээс шинэ хувилбар хэрэгтэй")

    if args.clean:
        log("Хуучин build/, dist/, .venv-г устгаж байна")
        for d in (ROOT / "build", DIST, VENV):
            shutil.rmtree(d, ignore_errors=True)

    if (ROOT / "hub.json").exists():
        log("hub.json (админы компьютерийн нэр, порт) програмд багцлагдана")
        refresh_hub_ips()
    else:
        print("АНХААР: hub.json алга - эхлээд админы компьютер дээр `python main.py --admin-setup` ажиллуулна "
              "(эсвэл ажилтан анх нээхэд админы компьютерийн нэрийг асууна)")
    if (ROOT / "share.json").exists():
        print("АНХААР: төслийн хавтаст хуучин share.json (нууц үгтэй) байна — хэрэггүй болсон, exe-д багцлагдахгүй. "
              "`--admin-setup` дахин ажиллуулахад устгагдана.")

    ensure_dist_writable()
    py = ensure_venv()
    log(f"PyInstaller-ээр бүтээж байна (хувилбар {app_version()})")
    run(py, "-m", "PyInstaller", ROOT / f"{APP}.spec", "--noconfirm")

    target = package_windows()
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
