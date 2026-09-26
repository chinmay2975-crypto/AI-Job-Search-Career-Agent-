@echo off
rem One-time setup: start the Career Agent servers at every Windows login, and put a
rem "Career Agent" shortcut on the desktop that opens the app. Undo with uninstall_autostart.bat.
setlocal
set "ROOT=%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$shell = New-Object -ComObject WScript.Shell;" ^
  "$startup = $shell.CreateShortcut([Environment]::GetFolderPath('Startup') + '\Career Agent servers.lnk');" ^
  "$startup.TargetPath = 'wscript.exe'; $startup.Arguments = '\"%ROOT%start_hidden.vbs\"';" ^
  "$startup.WorkingDirectory = '%ROOT%'; $startup.Description = 'Starts the Career Agent backend and UI'; $startup.Save();" ^
  "$desktop = $shell.CreateShortcut([Environment]::GetFolderPath('Desktop') + '\Career Agent.lnk');" ^
  "$desktop.TargetPath = 'wscript.exe'; $desktop.Arguments = '\"%ROOT%open_career_agent.vbs\"';" ^
  "$desktop.WorkingDirectory = '%ROOT%'; $desktop.IconLocation = 'shell32.dll,13'; $desktop.Description = 'Open the Career Agent'; $desktop.Save();" ^
  "Write-Output 'Installed: servers start at login; Career Agent shortcut is on the desktop.'"
if errorlevel 1 (
    echo Setup failed - see the message above.
    exit /b 1
)
rem Start the servers now too, so there's no need to log out and back in.
wscript "%ROOT%start_hidden.vbs"
