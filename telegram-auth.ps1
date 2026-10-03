$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$VenvDir = Join-Path $PSScriptRoot ".venv-telegram"
$PythonExe = Join-Path $VenvDir "Scripts\python.exe"
$Requirements = Join-Path $PSScriptRoot "tools\telegram_storage\requirements.txt"

if (-not (Test-Path $PythonExe)) {
    Write-Host "Creating local Telegram authorization environment..."
    python -m venv $VenvDir
}

& $PythonExe -m pip install --disable-pip-version-check -q -r $Requirements
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& $PythonExe .\tools\telegram_storage\auth_qr.py
exit $LASTEXITCODE
