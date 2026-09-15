# Sets up wkm on the Windows laptop (the machine you type on).
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

# Find a usable interpreter. The `py` launcher ships with the python.org
# installer but not with the Microsoft Store build or a plain `winget install`,
# so it cannot be assumed -- and a bare `python` may be the Store stub, which
# exists on PATH purely to open the Store. Probing settles both cases.
function Find-Python {
    $candidates = @(
        @{ Cmd = "py";       Prefix = @("-3") },
        @{ Cmd = "python";   Prefix = @() },
        @{ Cmd = "python3";  Prefix = @() }
    )
    foreach ($c in $candidates) {
        if (-not (Get-Command $c.Cmd -ErrorAction SilentlyContinue)) { continue }
        try {
            $probe = $c.Prefix + @("-c", "import sys; print('WKMOK' if sys.version_info >= (3,11) else sys.version.split()[0])")
            $out = & $c.Cmd @probe 2>$null
            if ($LASTEXITCODE -eq 0 -and $out -match "WKMOK") {
                return $c
            }
            if ($out -and $out -notmatch "WKMOK") {
                Write-Host ("  " + $c.Cmd + " is Python " + $out + " -- wkm needs 3.11 or newer") -ForegroundColor DarkYellow
            }
        } catch { }
    }
    return $null
}

$py = Find-Python
if (-not $py) {
    Write-Host ""
    Write-Host "No Python 3.11+ found." -ForegroundColor Red
    Write-Host "Install it from https://www.python.org/downloads/ and tick"
    Write-Host '"Add python.exe to PATH" during setup, then run this again.'
    exit 1
}
$pyName = $py.Cmd
if ($py.Prefix.Count -gt 0) { $pyName = $pyName + " " + ($py.Prefix -join " ") }
Write-Host ("Using " + $pyName) -ForegroundColor Cyan

$venvPython = Join-Path $root ".venv\Scripts\python.exe"
if (Test-Path $venvPython) {
    Write-Host "Reusing existing .venv" -ForegroundColor Cyan
} else {
    Write-Host "Creating virtual environment..." -ForegroundColor Cyan
    & $py.Cmd @($py.Prefix + @("-m", "venv", ".venv"))
    if (-not (Test-Path $venvPython)) {
        Write-Host "Failed to create .venv" -ForegroundColor Red
        exit 1
    }
}

Write-Host "Installing dependencies..." -ForegroundColor Cyan
& $venvPython -m pip install --upgrade pip --quiet
& $venvPython -m pip install -r requirements.txt --quiet

if (-not (Test-Path (Join-Path $root "wkm.toml"))) {
    Write-Host "Generating config..." -ForegroundColor Cyan
    & $venvPython -m wkm init
    Write-Host ""
    Write-Host "Copy wkm.toml to the Mac mini -- the passphrase must match." -ForegroundColor Yellow
} else {
    Write-Host "wkm.toml already exists; leaving it alone." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Checking the install..." -ForegroundColor Cyan
# --local on purpose: discovery cannot succeed until the Mac side is running,
# and a red FAIL here during setup reads as a broken install when it is not.
& $venvPython -m wkm doctor --local
Write-Host ""
Write-Host "Next: start 'wkm target' on the Mac mini, then run:" -ForegroundColor Green
Write-Host "    .\scripts\source.ps1"
exit 0
