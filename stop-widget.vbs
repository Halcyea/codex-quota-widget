Option Explicit
Dim fso, folder, runtime, request
Set fso = CreateObject("Scripting.FileSystemObject")
folder = fso.GetParentFolderName(WScript.ScriptFullName)
runtime = fso.BuildPath(folder, ".runtime")
If Not fso.FolderExists(runtime) Then fso.CreateFolder(runtime)
Set request = fso.CreateTextFile(fso.BuildPath(runtime, "stop.request"), True)
request.WriteLine "stop"
request.Close
