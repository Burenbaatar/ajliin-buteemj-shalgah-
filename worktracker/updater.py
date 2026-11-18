"""Автомат шинэчлэл: админы компьютерээс шинэ хувилбарыг татаж, програмыг солиод дахин эхлүүлнэ.

Эх үүсвэр: админы компьютерийн update/version.json + update/AjliinButeemj.exe — ажилтанд серверээр (hub.py),
админд өөрийн хавтаснаас; 3.1.x хувилбарууд хуучин хуваалцсан хавтаснаас (шилжилтийн үед).
    Админ `python build.py --publish` ажиллуулахад тэнд байршина. Тэр хавтсыг ажилтнууд зөвхөн уншина.

Ажиллаж буй .exe-г .old.exe болгож нэрлээд (Windows үүнийг зөвшөөрдөг) шинээр сольно.
Татсан файлыг version.json дахь SHA-256-аар шалгана.
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
from datetime import datetime
from pathlib import Path

from . import __version__
from .config import data_dir, is_frozen

log = logging.getLogger(__name__)
CHECK_EVERY_SEC = 30 * 60
UPDATE_DIR = "update"
VERSION_FILE = "version.json"
LEGACY_SHARE = Path(r"C:\AjliinButeemjShare")  # 3.1.x хувилбарууд шинэчлэлээ эндээс авна (build.py-тэй ижил)


def version_tuple(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v)[:3]) or (0,)


def asset_name() -> str:
    return "AjliinButeemj.exe"


def supported() -> bool:
    return is_frozen()


def check_share(share, current: str = __version__) -> dict | None:
    """Шинэ хувилбар байвал {"version", "path" | "url", "size", "sha256"}.
    Ажилтан: админы компьютерийн серверээс (client.HubClient). Админ: өөрийн хавтаснаас (share.ShareClient)."""
    if share is None:
        return None
    name = asset_name()
    if hasattr(share, "update_meta"):  # сервер
        meta = share.update_meta()
        source = {"url": share.update_url(name)} if name else {}
    else:  # хавтас
        if not share.available():
            return None
        folder = share.root / UPDATE_DIR
        try:
            meta = json.loads((folder / VERSION_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if name and not (folder / name).is_file():
            return None
        source = {"path": str(folder / name)} if name else {}
    if not meta or not name:
        return None
    version = str(meta.get("version", ""))
    if meta.get("file", name) != name or version_tuple(version) <= version_tuple(current) or not meta.get("sha256"):
        return None
    return {"version": version, **source, "size": int(meta.get("size") or 0), "sha256": str(meta["sha256"]).lower()}


def published_version(root: Path) -> str | None:
    """Админ: ажилтнуудад тараасан (update/version.json) хувилбар; хэзээ ч тараагаагүй бол None."""
    try:
        meta = json.loads((Path(root) / UPDATE_DIR / VERSION_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(meta, dict) or not meta.get("version"):
        return None
    return str(meta["version"])


def publish_self(root: Path) -> str:
    """Админ: одоо ажиллаж буй .exe-г update/ руу байршуулна (build.py --publish-тэй ижил) → ажилтнуудын програм
    30 минутын дотор өөрөө шинэчлэгдэнэ. Самбарын «Ажилтнуудад тараах» товч."""
    if not is_frozen():
        raise RuntimeError("Зөвхөн .exe-ээс тараана (Python-оор ажиллуулж байвал: python build.py --publish)")
    src, name = Path(sys.executable), asset_name()
    h = hashlib.sha256()
    with open(src, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    meta = {"version": __version__, "file": name, "size": src.stat().st_size, "sha256": h.hexdigest(),
            "published_at": datetime.now().isoformat(timespec="seconds")}
    folders = [Path(root) / UPDATE_DIR] + ([LEGACY_SHARE / UPDATE_DIR] if LEGACY_SHARE.is_dir() else [])
    for i, folder in enumerate(folders):
        try:
            folder.mkdir(parents=True, exist_ok=True)
            tmp = folder / (name + ".tmp")
            shutil.copy2(src, tmp)
            os.replace(tmp, folder / name)
            # version.json-г хамгийн сүүлд бичнэ — exe бүрэн хуулагдахаас өмнө хэн ч шинэчлэл эхлүүлэхгүй
            vtmp = folder / (VERSION_FILE + ".tmp")
            vtmp.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(vtmp, folder / VERSION_FILE)
        except OSError as exc:
            if i == 0:
                raise
            log.warning("3.1.x-ийн хавтсанд тарааж чадсангүй (%s): %s", folder, exc)
    log.info("Ажилтнуудад %s хувилбарыг тараалаа", __version__)
    return __version__


def _open_source(info: dict):
    if info.get("path"):
        return open(info["path"], "rb")
    import urllib.request

    return urllib.request.urlopen(info["url"], timeout=60)  # дотоод сүлжээ, SHA-256-аар шалгана


def download(info: dict, progress=None, dest_dir: Path | None = None) -> Path:
    """Хавтас / серверээс татаж, хэмжээ + SHA-256-г шалгана (өөр хүн сольсон/гэмтсэн бол татгалзана)."""
    dest_dir = dest_dir or (data_dir() / "update")
    dest_dir.mkdir(parents=True, exist_ok=True)
    final = dest_dir / (asset_name() or "update.bin")
    part = final.with_suffix(final.suffix + ".part")
    h = hashlib.sha256()
    done = 0
    total = int(info.get("size") or 0)
    with _open_source(info) as src, open(part, "wb") as f:
        while True:
            chunk = src.read(256 * 1024)
            if not chunk:
                break
            f.write(chunk)
            h.update(chunk)
            done += len(chunk)
            if progress:
                progress(done, total)
    if total and done != total:
        part.unlink(missing_ok=True)
        raise RuntimeError(f"Татсан файлын хэмжээ таарсангүй ({done} != {total})")
    if h.hexdigest() != info["sha256"]:
        part.unlink(missing_ok=True)
        raise RuntimeError("SHA-256 шалгалт таарсангүй — файл гэмтсэн эсвэл солигдсон")
    os.replace(part, final)
    return final


def apply(path: Path, exe: Path | None = None) -> None:
    """Шинэ хувилбарыг байрлуулж, дахин эхлүүлнэ. Дуудсаны дараа програм хаагдах ёстой."""
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
    if path.exists():
        path.unlink(missing_ok=True)


def cleanup() -> None:
    """Өмнөх шинэчлэлийн үлдэгдлийг цэвэрлэнэ (.old.exe)."""
    if not is_frozen():
        return
    try:
        exe = Path(sys.executable)
        exe.with_name(exe.stem + ".old.exe").unlink(missing_ok=True)
        shutil.rmtree(data_dir() / "update", ignore_errors=True)
    except OSError as exc:
        log.info("Шинэчлэлийн үлдэгдэл цэвэрлэж чадсангүй: %s", exc)
