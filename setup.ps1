# VA Stack setup and launch for Windows (PowerShell). Safe to run again.
#   .\setup.ps1              # set up, then open the app
#   .\setup.ps1 -NoLaunch    # set up only (or $env:VA_STACK_NO_LAUNCH = "1")
# It creates or reuses .venv in this folder, installs va-stack into it,
# checks the encryption, makes the per-user vault folder, and starts
# va-stack. It never reads, moves, overwrites or deletes the vault or any
# record file.
param([switch]$NoLaunch)
$ErrorActionPreference = "Stop"
$launch = -not ($NoLaunch -or $env:VA_STACK_NO_LAUNCH -eq "1")
Set-Location -LiteralPath $PSScriptRoot

Write-Host "Step 1 of 4: Python virtual environment (.venv)"
if (Test-Path -LiteralPath ".venv\Scripts\python.exe") {
    Write-Host "  reusing .venv"
} else {
    py -m venv .venv
    if ($LASTEXITCODE -ne 0) { exit 1 }
}
. .\.venv\Scripts\Activate.ps1

Write-Host "Step 2 of 4: install va-stack and its packages"
python -m pip install -e .
if ($LASTEXITCODE -ne 0) { exit 1 }

Write-Host "Step 3 of 4: check the encryption (SQLCipher)"
$version = python -c "import sqlcipher3; print(sqlcipher3.connect(':memory:').execute('PRAGMA cipher_version').fetchone()[0])"
if ($LASTEXITCODE -ne 0 -or -not $version) {
    Write-Error "no encryption found; stopping (the vault will not run without it)"
    exit 1
}
Write-Host "  SQLCipher $version"

Write-Host "Step 4 of 4: your vault folder (an existing vault is left as is)"
python -c "import app_paths; p = app_paths.default_vault_path(); print('  vault:', p, '(exists)' if p.exists() else '(will be created in the app)')"
if ($LASTEXITCODE -ne 0) { exit 1 }

if ($launch) {
    va-stack
    exit $LASTEXITCODE
}
Write-Host "Setup done. Start the app with: .venv\Scripts\Activate.ps1; va-stack"
