"""Ажлын бүтээмж — эхлүүлэх файл.

    python main.py                 # цонхтой ажиллуулах
    python main.py --background    # цонхгүй, зөвхөн tray (компьютер асахад автоматаар ингэж эхэлнэ)
    python main.py --admin-setup   # энэ компьютерийг админых болгох (хуваалцсан хавтас, админ нууц үг)
    python main.py --no-gui        # терминал дээр
    python main.py --camera-test   # камер, царай илрүүлэлтийг шалгах
    python main.py --selftest      # камер нээхгүйгээр бүх модулийг шалгах
    python main.py --report [YYYY-MM-DD]   # тайлан үүсгэж нээх
    python main.py --send   [YYYY-MM-DD]   # тайланг имэйл/Telegram-аар илгээх
"""
from __future__ import annotations

import argparse
import sys
import webbrowser
from datetime import date

from worktracker import APP_TITLE, __version__
from worktracker.config import data_dir, load_admin, load_config, load_share
from worktracker.system import (enable_hidpi, ensure_single_instance, set_autostart, setup_logging,
                                signal_running_instance)


def _make_client(cfg):
    from worktracker.share import ShareClient, make_uid

    info = load_share()
    return ShareClient(info, make_uid(), cfg["employee_name"]) if info else None


def selftest(cfg, log_path) -> int:
    """Камер нээхгүйгээр модулиудыг шалгана (суулгасны дараа шалгахад)."""
    import logging
    import platform

    from worktracker import activity
    from worktracker.presence import FaceDetector
    from worktracker.share import make_uid

    ok = True
    lines = [f"{APP_TITLE} {__version__} — {platform.system()} {platform.release()}"]
    for name, fn in [
        ("Идэвхтэй апп", activity.get_active_app),
        ("Идэвхгүй (сек)", activity.get_idle_seconds),
        ("Түгжээтэй", activity.is_screen_locked),
        ("Царай илрүүлэгч", lambda: type(FaceDetector()).__name__ + " OK"),
    ]:
        try:
            lines.append(f"  {name:<16}: {fn()}")
        except Exception as exc:  # noqa: BLE001
            ok = False
            lines.append(f"  {name:<16}: АЛДАА {exc!r}")

    lines.append(f"  Ажилтан         : {cfg['employee_name'] or '(анх нээхэд асууна)'} [{make_uid()}]")
    client = _make_client(cfg)
    if client is None:
        lines.append("  Админы хавтас   : тохируулаагүй (share.json алга)")
    else:
        state = "холбогдсон" if client.available() else "олдсонгүй (админы компьютер унтраалттай?)"
        lines.append(f"  Админы хавтас   : {client.root} — {state}")
    lines += [
        f"  Админ компьютер : {'тийм' if load_admin() else 'үгүй'}",
        f"  Тайлан илгээх   : имэйл={cfg['email']['enabled']}, Telegram={cfg['telegram']['enabled']}",
        f"  Тохиргоо        : {cfg.manager_path or cfg.path}",
        f"  Өгөгдөл         : {data_dir()}",
        f"  Лог             : {log_path}",
    ]
    text = "\n".join(lines)
    if sys.stdout is not None:
        print(text)
    else:  # цонхтой .exe: консол байхгүй тул лог файлд бичнэ
        logging.getLogger("selftest").info("\n%s", text)
    return 0 if ok else 1


def run_headless(cfg) -> int:
    from worktracker.app import App

    if not cfg["employee_name"]:
        cfg["employee_name"] = input("Таны овог нэр: ").strip()
        cfg.save()
    client = _make_client(cfg)
    if client is None:
        print("Админы хавтас тохируулаагүй (share.json) — зөвхөн энэ компьютерт хадгална.")
    App(cfg, api=client, admin=load_admin() is not None).run_headless()
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=APP_TITLE)
    p.add_argument("--background", action="store_true", help="цонхгүй, зөвхөн tray дүрстэй эхлэх")
    p.add_argument("--admin-setup", action="store_true", help="энэ компьютерийг админых болгох")
    p.add_argument("--admin-setup-system", metavar="FILE", help=argparse.SUPPRESS)  # UAC-аар эхэлсэн хэсэг
    p.add_argument("--no-gui", action="store_true", help="цонхгүй ажиллуулах (терминал)")
    p.add_argument("--camera-test", action="store_true", help="камерын тест")
    p.add_argument("--report", nargs="?", const=date.today().isoformat(), metavar="ӨДӨР", help="тайлан үүсгэх")
    p.add_argument("--send", nargs="?", const=date.today().isoformat(), metavar="ӨДӨР", help="тайлан илгээх")
    p.add_argument("--selftest", action="store_true", help="камер нээхгүйгээр бүх модулийг шалгах")
    p.add_argument("--updated", action="store_true", help=argparse.SUPPRESS)  # шинэчлэлийн дараа эхлэх
    p.add_argument("--version", action="version", version=__version__)
    args, _ = p.parse_known_args()  # macOS .app-д нэмэлт аргумент ирж болно

    log_path = setup_logging()

    if args.admin_setup_system:
        from worktracker.admin_setup import run_system_step

        return run_system_step(args.admin_setup_system)
    if args.admin_setup:
        from worktracker.admin_setup import run_admin_setup

        enable_hidpi()
        return run_admin_setup()

    cfg = load_config()

    if args.camera_test:
        from worktracker.presence import camera_test

        camera_test(cfg["camera_index"])
        return 0

    if args.selftest:
        return selftest(cfg, log_path)

    if args.report or args.send:
        from worktracker.app import storage_path
        from worktracker.notify import send_report
        from worktracker.report import build_report, save_report
        from worktracker.share import make_uid
        from worktracker.storage import Storage

        storage = Storage(storage_path(make_uid()))
        day = args.send or args.report
        if args.send:
            ok, msg = send_report(cfg, storage, day)
            print(msg)
            return 0 if ok else 1
        html_path, csv_path = save_report(build_report(storage, day, cfg["employee_name"], cfg["min_away_period_min"]))
        print(f"Тайлан: {html_path}\nCSV:    {csv_path}")
        webbrowser.open(html_path.as_uri())
        return 0

    if not ensure_single_instance(wait_sec=20 if args.updated else 0):
        # Аль хэдийн ажиллаж байна → (автоматаар эхэлсэн биш бол) тэр цонхыг гаргаж ирээд гарна
        if args.background or signal_running_instance():
            return 0
        msg = "Програм аль хэдийн ажиллаж байна."
        if args.no_gui:
            print(msg)
        else:
            import tkinter as tk
            from tkinter import messagebox

            root = tk.Tk()
            root.withdraw()
            messagebox.showinfo(APP_TITLE, msg)
        return 0

    set_autostart(bool(cfg["autostart"]))
    # .exe (цонхтой) үед sys.stdout=None → print юу ч хийхгүй
    print(f"Тохиргоо: {cfg.manager_path or cfg.path}\nЛог:      {log_path}")

    if args.no_gui:
        return run_headless(cfg)
    enable_hidpi()
    from worktracker.gui import run_gui

    run_gui(cfg, updated=args.updated, background=args.background or args.updated)
    return 0


if __name__ == "__main__":
    sys.exit(main())
