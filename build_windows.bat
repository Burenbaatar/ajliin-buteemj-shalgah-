@echo off
chcp 65001 >nul
REM Windows дээр нэг .exe файл бүтээнэ -> dist\AjliinButeemj.exe
cd /d "%~dp0"
if not exist .venv python -m venv .venv
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt pyinstaller
pyinstaller AjliinButeemj.spec --noconfirm
REM Тараах хавтас: .exe + менежерийн config.json
if exist config.json (
  copy /y config.json dist\config.json >nul
) else (
  copy /y config.example.json dist\config.example.json >nul
  echo АНХААР: config.json алга - тараахаас өмнө dist\config.json үүсгэнэ үү
)
echo.
echo Бэлэн: dist\AjliinButeemj.exe + dist\config.json
pause
