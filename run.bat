@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ====================================
echo   Note.Money UI starting...
echo ====================================
echo.
echo If browser does not open, go to:
echo   http://localhost:8501
echo.
python -m streamlit run ui_streamlit.py
echo.
echo ------------------------------------
echo  Streamlit exited. Check errors above.
echo ------------------------------------
pause
