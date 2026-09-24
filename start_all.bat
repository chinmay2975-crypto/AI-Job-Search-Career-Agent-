@echo off
cd /d "%~dp0"
start "career-agent-backend" /min cmd /c start_backend.bat
start "career-agent-frontend" /min cmd /c start_frontend.bat
