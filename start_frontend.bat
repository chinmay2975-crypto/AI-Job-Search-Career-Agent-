@echo off
setlocal

cd /d "%~dp0"
if not exist logs mkdir logs

:loop
rem Another copy already serving port 8501 (e.g. started at login): leave it alone.
netstat -ano | findstr /C:":8501 " | findstr LISTENING >nul
if %errorlevel%==0 (
    echo %date% %time% - streamlit already running on port 8501, not starting another >> logs\launcher.log
    exit /b 0
)
echo %date% %time% - starting streamlit >> logs\streamlit.log
".venv\Scripts\python.exe" -m streamlit run app.py --server.port 8501 --server.address 127.0.0.1 --server.headless true >> logs\streamlit.log 2>&1
echo %date% %time% - streamlit exited, restarting in 5s... >> logs\streamlit.log
timeout /t 5 /nobreak >nul
goto loop
