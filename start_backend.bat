@echo off
setlocal

cd /d "%~dp0"
if not exist logs mkdir logs

:loop
rem Another copy already serving port 8010 (e.g. started at login): leave it alone.
netstat -ano | findstr /C:"127.0.0.1:8010 " | findstr LISTENING >nul
if %errorlevel%==0 (
    echo %date% %time% - backend already running on port 8010, not starting another >> logs\launcher.log
    exit /b 0
)
echo %date% %time% - starting uvicorn >> logs\uvicorn.log
".venv\Scripts\python.exe" -m uvicorn main:app --host 127.0.0.1 --port 8010 >> logs\uvicorn.log 2>&1
echo %date% %time% - uvicorn exited, restarting in 5s... >> logs\uvicorn.log
timeout /t 5 /nobreak >nul
goto loop
