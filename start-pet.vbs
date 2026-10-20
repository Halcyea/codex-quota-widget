Option Explicit
Dim fso, shell, folder, appPath, pythonw, candidate, directory, command, result
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
folder = fso.GetParentFolderName(WScript.ScriptFullName)
appPath = fso.BuildPath(folder, "app.py")
If Not fso.FileExists(appPath) Then
    MsgBox "Pet Energy: app.py was not found next to this launcher.", 16, "Pet Energy"
    WScript.Quit 2
End If

pythonw = ""
candidate = fso.BuildPath(folder, ".venv\Scripts\pythonw.exe")
If fso.FileExists(candidate) Then pythonw = candidate
candidate = shell.ExpandEnvironmentStrings("%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe")
If pythonw = "" And fso.FileExists(candidate) Then pythonw = candidate
If pythonw = "" Then
    For Each directory In Split(shell.ExpandEnvironmentStrings("%PATH%"), ";")
        directory = Replace(Trim(directory), Chr(34), "")
        If Len(directory) > 0 Then
            candidate = fso.BuildPath(directory, "pythonw.exe")
            If fso.FileExists(candidate) Then
                pythonw = candidate
                Exit For
            End If
        End If
    Next
End If
If pythonw = "" Then
    MsgBox "Pet Energy: Python was not found. Install Python with Tk and Pillow first.", 16, "Pet Energy"
    WScript.Quit 3
End If

shell.CurrentDirectory = folder
shell.Environment("Process")("PYTHONUTF8") = "1"
command = Chr(34) & pythonw & Chr(34) & " " & Chr(34) & appPath & Chr(34)
On Error Resume Next
result = shell.Run(command, 0, False)
If Err.Number <> 0 Then
    MsgBox "Pet Energy could not start: " & Err.Description, 16, "Pet Energy"
    WScript.Quit 4
End If
On Error GoTo 0
