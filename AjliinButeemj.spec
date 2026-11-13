# -*- mode: python ; coding: utf-8 -*-
# PyInstaller тохиргоо.  Ажиллуулах:  pyinstaller AjliinButeemj.spec --noconfirm
#   Windows дээр -> dist/AjliinButeemj.exe   (нэг файл)
#   macOS дээр   -> dist/AjliinButeemj.app
# Анхаар: Windows .exe-г зөвхөн Windows дээр, .app-г зөвхөн Mac дээр бүтээнэ.
import os
import sys

APP = "AjliinButeemj"
VERSION = "3.1.0"

datas = [("assets/face_detection_yunet_2023mar.onnx", "assets")]
# share.json (админы хуваалцсан хавтас, --admin-setup үүсгэнэ) байвал програмд багцална → ажилтан юу ч тохируулахгүй
if os.path.exists("share.json"):
    datas.append(("share.json", "."))

a = Analysis(
    ["main.py"],
    datas=datas,
    hiddenimports=["pystray._win32"] if sys.platform == "win32" else [],
    excludes=["matplotlib", "scipy", "pandas", "IPython", "pytest"] + ([] if sys.platform == "win32" else ["PIL"]),
    noarchive=False,
)
pyz = PYZ(a.pure)

if sys.platform == "darwin":
    exe = EXE(
        pyz, a.scripts, [],
        exclude_binaries=True, name=APP, console=False, upx=False, argv_emulation=False,
    )
    coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name=APP)
    app = BUNDLE(
        coll,
        name=f"{APP}.app",
        bundle_identifier="mn.ajliinbuteemj",
        version=VERSION,
        info_plist={
            "CFBundleDisplayName": "Ажлын бүтээмж",
            "NSHighResolutionCapable": True,
            # Үүнгүй бол macOS камерт хандахыг хориглоно
            "NSCameraUsageDescription": "Ажилтан компьютерийн өмнө байгаа эсэхийг шалгахад камер ашиглана. "
            "Зураг, бичлэг хадгалахгүй.",
            "NSCameraUseContinuityCameraDeviceType": True,
        },
    )
else:
    exe = EXE(
        pyz, a.scripts, a.binaries, a.datas, [],
        name=APP, console=False, upx=False, runtime_tmpdir=None,
    )
