@echo off
chcp 65001 >nul
REM Windows дээр нэг .exe файл бүтээнэ -> dist\AjliinButeemj.exe
cd /d "%~dp0"
if not exist server.json echo АНХААР: server.json алга - програм анх нээхэд Supabase URL/key асууна (README үз)
if not exist .venv python -m venv .venv
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt pyinstaller
pyinstaller AjliinButeemj.spec --noconfirm
REM Имэйл/Telegram тохируулсан бол config.json-г .exe-ийн хажууд хуулна
if exist config.json copy /y config.json dist\config.json >nul
echo.
echo Бэлэн: dist\AjliinButeemj.exe
pause
