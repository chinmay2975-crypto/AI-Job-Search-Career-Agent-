' Starts the Career Agent backend (port 8010) and Streamlit UI (port 8501) without console windows.
' Each server restarts itself if it crashes, and exits straight away if a copy is already running.
' Logs: logs\uvicorn.log and logs\streamlit.log. Run at login via install_autostart.bat.
Set shell = CreateObject("WScript.Shell")
root = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
shell.Run "cmd /c """ & root & "\start_backend.bat""", 0, False
shell.Run "cmd /c """ & root & "\start_frontend.bat""", 0, False
