' Desktop shortcut target: make sure both servers are running, then open the app in the browser.
Set shell = CreateObject("WScript.Shell")
root = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
shell.Run "wscript """ & root & "\start_hidden.vbs""", 0, True

' Wait (up to ~60s on a cold start) until the UI and the backend both answer.
For i = 1 To 60
    If IsUp("http://127.0.0.1:8501/") And IsUp("http://127.0.0.1:8010/health") Then Exit For
    WScript.Sleep 1000
Next
shell.Run "http://localhost:8501"

Function IsUp(url)
    On Error Resume Next
    Set http = CreateObject("MSXML2.ServerXMLHTTP")
    http.setTimeouts 1000, 1000, 2000, 2000
    http.Open "GET", url, False
    http.Send
    IsUp = (Err.Number = 0)
    If IsUp Then IsUp = (http.Status = 200)
    On Error GoTo 0
End Function
