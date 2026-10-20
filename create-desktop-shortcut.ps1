$ErrorActionPreference = 'Stop'
$petProject = $PSScriptRoot
$petLauncher = Join-Path $petProject 'start-pet.vbs'
if (-not (Test-Path -LiteralPath $petLauncher -PathType Leaf)) {
    throw 'Pet Energy launcher was not found.'
}
$petDesktop = [Environment]::GetFolderPath('DesktopDirectory')
if (-not $petDesktop -or -not (Test-Path -LiteralPath $petDesktop -PathType Container)) {
    throw 'Windows desktop directory was not found.'
}
$petWscript = Join-Path ([Environment]::SystemDirectory) 'wscript.exe'
$petShortcutPath = Join-Path $petDesktop '额度挂件.lnk'
$petShell = New-Object -ComObject WScript.Shell
if (Test-Path -LiteralPath $petShortcutPath) {
    $petExisting = $petShell.CreateShortcut($petShortcutPath)
    if ($petExisting.Arguments -ne ('"' + $petLauncher + '"')) {
        $petShortcutPath = Join-Path $petDesktop 'Codex额度挂件.lnk'
        $petSuffix = 2
        while (Test-Path -LiteralPath $petShortcutPath) {
            $petCandidate = $petShell.CreateShortcut($petShortcutPath)
            if ($petCandidate.Arguments -eq ('"' + $petLauncher + '"')) { break }
            $petShortcutPath = Join-Path $petDesktop ("Codex额度挂件 ($petSuffix).lnk")
            $petSuffix++
        }
    }
}
$petShortcut = $petShell.CreateShortcut($petShortcutPath)
$petShortcut.TargetPath = $petWscript
$petShortcut.Arguments = '"' + $petLauncher + '"'
$petShortcut.WorkingDirectory = $petProject
$petShortcut.WindowStyle = 7
$petShortcut.Description = 'Codex额度挂件：查看 Codex 5 小时和周剩余额度'
$petIcon = @('powercpl.dll', 'powercfg.cpl', 'shell32.dll') |
    ForEach-Object { Join-Path ([Environment]::SystemDirectory) $_ } |
    Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } |
    Select-Object -First 1
if ($petIcon) { $petShortcut.IconLocation = $petIcon + ',0' }
$petShortcut.Save()
$petReadback = $petShell.CreateShortcut($petShortcutPath)
if ($petReadback.TargetPath -ne $petWscript -or $petReadback.Arguments -ne ('"' + $petLauncher + '"')) {
    throw 'Desktop shortcut verification failed.'
}
[pscustomobject]@{
    Shortcut = $petShortcutPath
    Target = $petReadback.TargetPath
    Arguments = $petReadback.Arguments
    WorkingDirectory = $petReadback.WorkingDirectory
    Verified = $true
} | ConvertTo-Json
