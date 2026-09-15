# Starts wkm on the machine whose keyboard and touchpad you use.
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$venvPython = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Host "No .venv here yet. Run .\scripts\setup-windows.ps1 first." -ForegroundColor Red
    exit 1
}
& $venvPython -m wkm source @args
