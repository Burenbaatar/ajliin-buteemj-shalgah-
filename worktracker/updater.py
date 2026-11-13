"""Автомат шинэчлэл: шинэ хувилбарыг татаж, програмыг солиод дахин эхлүүлнэ.

Эх үүсвэр (анхдагч): админы хуваалцсан хавтас — update/version.json + update/AjliinButeemj.exe.
    Админ `python build.py --publish` ажиллуулахад тэнд байршина. Тэр хавтсыг ажилтнууд зөвхөн уншина.
Эх үүсвэр (заавал биш): config.json-д "update_repo" өгсөн бол GitHub Releases.

- Windows: ажиллаж буй .exe-г .old.exe болгож нэрлээд (Windows үүнийг зөвшөөрдөг) шинээр сольно.
- macOS:   шинэ .app-г задлаад хуучныг .old болгож сольно.
Татсан файлыг SHA-256-аар шалгана.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

from . import __version__
from .config import data_dir, is_frozen
from .net import ssl_context

log = logging.getLogger(__name__)
CHECK_EVERY_SEC = 30 * 60
UPDATE_DIR = "update"
VERSION_FILE = "version.json"


def version_tuple(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v)[:3]) or (0,)


def asset_name() -> str | None:
    if sys.platform == "win32":
        return "AjliinButeemj.exe"
    if sys.platform == "darwin":
        return "AjliinButeemj-mac.zip"
    return None


def supported() -> bool:
    return is_frozen() and asset_name() is not None


def _get(url: str, timeout: float = 15):
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                               "User-Agent": f"AjliinButeemj/{__version__}"})
    return urllib.request.urlopen(req, timeout=timeout, context=ssl_context())


def check_any(cfg: dict, share) -> dict | None:
    """config.json-д update_repo өгсөн бол GitHub, үгүй бол админы хавтас."""
    repo = str(cfg.get("update_repo") or "").strip()
    return check(repo) if repo else check_share(share)


def check_share(share, current: str = __version__) -> dict | None:
    """Админы хавтасны update/ дотор шинэ хувилбар байвал {"version", "path", "size", "sha256"}."""
    if share is None or not share.available():
        return None
    folder = share.root / UPDATE_DIR
    try:
        meta = json.loads((folder / VERSION_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    name = asset_name()
    version = str(meta.get("version", ""))
    if not name or meta.get("file", name) != name or version_tuple(version) <= version_tuple(current):
        return None
    path = folder / name
    if not path.is_file() or not meta.get("sha256"):
        return None
    return {"version": version, "path": str(path), "size": int(meta.get("size") or 0),
            "sha256": str(meta["sha256"]).lower()}


def check(repo: str, api: str = "https://api.github.com", current: str = __version__) -> dict | None:
    """Шинэ хувилбар байвал {"version", "url", "size", "sha_url"}, үгүй бол None."""
    with _get(f"{api}/repos/{repo}/releases/latest", timeout=10) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    tag = str(data.get("tag_name", ""))
    if version_tuple(tag) <= version_tuple(current):
        return None
    assets = {a["name"]: a for a in data.get("assets", [])}
    name = asset_name()
    if name not in assets:
        log.info("Шинэ хувилбар %s байна, гэхдээ %s файл алга", tag, name)
        return None
    sha = assets.get(name + ".sha256")
    return {"version": tag.lstrip("v"), "url": assets[name]["browser_download_url"],
            "size": int(assets[name].get("size") or 0), "sha_url": sha["browser_download_url"] if sha else None}


def download(info: dict, progress=None, dest_dir: Path | None = None) -> Path:
    dest_dir = dest_dir or (data_dir() / "update")
    dest_dir.mkdir(parents=True, exist_ok=True)
    final = dest_dir / (asset_name() or "update.bin")
    part = final.with_suffix(final.suffix + ".part")
    h = hashlib.sha256()
    done = 0
    source = open(info["path"], "rb") if info.get("path") else _get(info["url"], timeout=60)
    with source as resp, open(part, "wb") as f:
        total = int(info.get("size") or 0) if info.get("path") else \
            int(resp.headers.get("Content-Length") or info.get("size") or 0)
        while True:
            chunk = resp.read(256 * 1024)
            if not chunk:
                break
            f.write(chunk)
            h.update(chunk)
            done += len(chunk)
            if progress:
                progress(done, total)
    if info.get("size") and done != info["size"]:
        part.unlink(missing_ok=True)
        raise RuntimeError(f"Татсан файлын хэмжээ таарсангүй ({done} != {info['size']})")
    expected = info.get("sha256")
    if not expected and info.get("sha_url"):
        with _get(info["sha_url"]) as resp:
            expected = resp.read().decode("utf-8").split()[0].strip().lower()
    if expected:
        if h.hexdigest() != expected:
            part.unlink(missing_ok=True)
            raise RuntimeError("SHA-256 шалгалт таарсангүй — файл гэмтсэн")
    os.replace(part, final)
    return final


def _mac_bundle() -> Path | None:
    exe = Path(sys.executable).resolve()
    return exe.parents[2] if exe.parent.name == "MacOS" else None


def apply(path: Path, exe: Path | None = None) -> None:
    """Шинэ хувилбарыг байрлуулж, дахин эхлүүлнэ. Дуудсаны дараа програм хаагдах ёстой."""
    if sys.platform == "win32":
        exe = exe or Path(sys.executable)
        old = exe.with_name(exe.stem + ".old.exe")
        old.unlink(missing_ok=True)
        exe.rename(old)
        try:
            shutil.move(str(path), str(exe))
        except OSError:
            old.rename(exe)  # буцааж сэргээнэ
            raise
        flags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        subprocess.Popen([str(exe), "--updated"], creationflags=flags, close_fds=True)
    elif sys.platform == "darwin":
        bundle = exe or _mac_bundle()
        if bundle is None:
            raise RuntimeError(".app олдсонгүй")
        tmp = Path(tempfile.mkdtemp(dir=data_dir()))
        subprocess.run(["/usr/bin/ditto", "-x", "-k", str(path), str(tmp)], check=True)
        new_app = next((p for p in tmp.rglob("*.app") if p.name == bundle.name), None) or next(tmp.rglob("*.app"))
        backup = bundle.with_name(bundle.name + ".old")
        shutil.rmtree(backup, ignore_errors=True)
        bundle.rename(backup)
        try:
            shutil.move(str(new_app), str(bundle))
        except OSError:
            backup.rename(bundle)
            raise
        subprocess.run(["/usr/bin/xattr", "-dr", "com.apple.quarantine", str(bundle)], capture_output=True)
        shutil.rmtree(tmp, ignore_errors=True)
        subprocess.Popen(["/usr/bin/open", "-n", str(bundle), "--args", "--updated"])
    else:
        raise RuntimeError("Энэ системд автомат шинэчлэл дэмжигдэхгүй")
    if path.exists():
        path.unlink(missing_ok=True)


def cleanup() -> None:
    """Өмнөх шинэчлэлийн үлдэгдлийг цэвэрлэнэ (.old.exe / .app.old)."""
    if not is_frozen():
        return
    try:
        if sys.platform == "win32":
            exe = Path(sys.executable)
            exe.with_name(exe.stem + ".old.exe").unlink(missing_ok=True)
        elif sys.platform == "darwin" and _mac_bundle():
            b = _mac_bundle()
            shutil.rmtree(b.with_name(b.name + ".old"), ignore_errors=True)
        shutil.rmtree(data_dir() / "update", ignore_errors=True)
    except OSError as exc:
        log.info("Шинэчлэлийн үлдэгдэл цэвэрлэж чадсангүй: %s", exc)
