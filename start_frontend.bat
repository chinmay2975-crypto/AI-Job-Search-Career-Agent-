@echo off
setlocal

cd /d "%~dp0"
if not exist logs mkdir logs

:loop
echo %date% %time% - starting streamlit >> logs\streamlit.log
".venv\Scripts\python.exe" -m streamlit run app.py --server.port 8501 --server.headless true >> logs\streamlit.log 2>&1
echo %date% %time% - streamlit exited, restarting in 5s... >> logs\streamlit.log
timeout /t 5 /nobreak >nul
goto loop
