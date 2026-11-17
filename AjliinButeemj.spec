# -*- mode: python ; coding: utf-8 -*-
# PyInstaller тохиргоо.  Ажиллуулах:  pyinstaller AjliinButeemj.spec --noconfirm
#   -> dist/AjliinButeemj.exe   (нэг файл, зөвхөн Windows)
import os

APP = "AjliinButeemj"
VERSION = "3.4.0"

datas = [("assets/face_detection_yunet_2023mar.onnx", "assets"),
         ("assets/face_antispoof_minifasnetv2.onnx", "assets"),
         ("assets/face_antispoof_LICENSE.txt", "assets"),
         ("assets/face_recognition_sface_2021dec.onnx", "assets"),
         ("assets/face_recognition_sface_LICENSE.txt", "assets")]
# hub.json (админы компьютерийн нэр + порт, нууц зүйлгүй; --admin-setup үүсгэнэ) байвал програмд багцална
if os.path.exists("hub.json"):
    datas.append(("hub.json", "."))

a = Analysis(
    ["main.py"],
    datas=datas,
    hiddenimports=["pystray._win32"],
    excludes=["matplotlib", "scipy", "pandas", "IPython", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name=APP, console=False, upx=False, runtime_tmpdir=None,
)
