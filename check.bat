@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ====================================
echo   Environment check
echo ====================================
echo.
echo [Python version]
python --version
echo.
echo [pip list - key packages]
python -m pip show streamlit pandas pykrx yfinance 2>nul | findstr /C:"Name:" /C:"Version:"
echo.
echo [Trying to import ui_streamlit modules]
python -c "from src import db, accounts, prices, maturity, valuation, projection, seed, report; print('All imports OK')"
echo.
pause
