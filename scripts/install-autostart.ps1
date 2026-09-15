# Makes wkm start with Windows, minimised. Run once.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$bat = Join-Path $root "wkm.bat"
if (-not (Test-Path $bat)) { Write-Host "Cannot find '$bat'" -ForegroundColor Red; exit 1 }

$startup = [Environment]::GetFolderPath("Startup")
$link = Join-Path $startup "wkm.lnk"

$shell = New-Object -ComObject WScript.Shell
$sc = $shell.CreateShortcut($link)
$sc.TargetPath = $bat
$sc.WorkingDirectory = $root
$sc.WindowStyle = 7          # minimised
$sc.Description = "wkm - share keyboard and touchpad with the Mac mini"
$sc.Save()

Write-Host "Done. wkm will start with Windows." -ForegroundColor Green
Write-Host "Shortcut: $link"
Write-Host "To undo, delete that file (or run: shell:startup)."
