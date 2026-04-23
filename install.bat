@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Installing dependencies...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
echo.
echo Done. Double-click run.bat to start the UI.
pause
