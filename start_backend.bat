@echo off
setlocal

cd /d "%~dp0"
if not exist logs mkdir logs

:loop
echo %date% %time% - starting uvicorn >> logs\uvicorn.log
".venv\Scripts\python.exe" -m uvicorn main:app --host 127.0.0.1 --port 8000 >> logs\uvicorn.log 2>&1
echo %date% %time% - uvicorn exited, restarting in 5s... >> logs\uvicorn.log
timeout /t 5 /nobreak >nul
goto loop
