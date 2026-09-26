@echo off
rem Undo install_autostart.bat: stop starting the servers at login and remove the desktop shortcut.
rem Servers already running keep running until you log out (or end python.exe in Task Manager).
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "Remove-Item -ErrorAction SilentlyContinue ([Environment]::GetFolderPath('Startup') + '\Career Agent servers.lnk');" ^
  "Remove-Item -ErrorAction SilentlyContinue ([Environment]::GetFolderPath('Desktop') + '\Career Agent.lnk');" ^
  "Write-Output 'Removed the login autostart and the desktop shortcut.'"
