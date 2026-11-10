#!/bin/bash
# macOS дээр .app бүтээнэ -> dist/AjliinButeemj.app (+ тараахад зориулсан .zip)
set -e
cd "$(dirname "$0")"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt pyinstaller
.venv/bin/pyinstaller AjliinButeemj.spec --noconfirm

# Тараах хавтас: .app + менежерийн config.json
PKG=dist/AjliinButeemj-mac
rm -rf "$PKG" dist/AjliinButeemj-mac.zip
mkdir -p "$PKG"
ditto dist/AjliinButeemj.app "$PKG/AjliinButeemj.app"
if [ -f config.json ]; then
  cp config.json "$PKG/config.json"
else
  cp config.example.json "$PKG/config.example.json"
  echo "АНХААР: config.json алга — тараахаас өмнө $PKG/config.json үүсгэнэ үү"
fi
ditto -c -k --keepParent "$PKG" dist/AjliinButeemj-mac.zip
echo
echo "Бэлэн: $PKG/  (тараах: dist/AjliinButeemj-mac.zip)"
