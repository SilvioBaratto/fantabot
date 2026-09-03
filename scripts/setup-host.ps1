<#
  fantabot - host Python environment setup (Windows)
  -------------------------------------------------------------------
  Installs Python 3.11, creates a .venv in the repo root, installs the package
  editable with the dev extras, downloads Chromium for Playwright, and verifies.

  The CLAUDE.md doc assumes `conda activate fanta`; a plain venv works just as
  well - pyproject.toml is an ordinary Python project.

  Usage:
    pwsh -File scripts\setup-host.ps1
    powershell -ExecutionPolicy Bypass -File scripts\setup-host.ps1

  Notes:
    - `winget install` may raise a UAC prompt.
    - winget does not refresh the PATH of the running shell; if `py -3.11` is
      still not found afterwards, open a new terminal and re-run this script.
    - The full test suite is designed to run in Linux via Docker
      (`docker compose run --rm tests pytest -q`). On a Windows host a subset
      fails on known quirks (path separators, cp1252 reads, CRLF hashes).
#>

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot

Write-Host '== 1/6  Checking for Python 3.11 ==' -ForegroundColor Cyan
$hasPy311 = $false
try { & py -3.11 --version *> $null; $hasPy311 = ($LASTEXITCODE -eq 0) } catch {}

if (-not $hasPy311) {
    Write-Host 'Python 3.11 not found. Installing via winget...' -ForegroundColor Yellow
    winget install --id Python.Python.3.11 --exact --source winget `
        --accept-package-agreements --accept-source-agreements --disable-interactivity
    $env:Path = [Environment]::GetEnvironmentVariable('Path','Machine') + ';' +
                [Environment]::GetEnvironmentVariable('Path','User')
    try { & py -3.11 --version *> $null; $hasPy311 = ($LASTEXITCODE -eq 0) } catch {}
    if (-not $hasPy311) {
        throw 'py -3.11 still unavailable. Open a new terminal and re-run this script.'
    }
}
Write-Host "OK: $(& py -3.11 --version)" -ForegroundColor Green

Write-Host '== 2/6  Creating the .venv virtualenv ==' -ForegroundColor Cyan
Set-Location $repo
if (-not (Test-Path '.venv')) { & py -3.11 -m venv .venv }
$vpy = Join-Path $repo '.venv\Scripts\python.exe'

Write-Host '== 3/6  Upgrading pip ==' -ForegroundColor Cyan
& $vpy -m pip install --upgrade pip

Write-Host '== 4/6  Installing fantabot (editable) + dev extras ==' -ForegroundColor Cyan
& $vpy -m pip install -e ".[dev]"

Write-Host '== 5/6  Playwright: downloading Chromium ==' -ForegroundColor Cyan
& $vpy -m playwright install chromium

Write-Host '== 6/6  Verifying ==' -ForegroundColor Cyan
if (-not (Test-Path '.env')) {
    Copy-Item '.env.example' '.env'
    Write-Host 'Created .env from .env.example - fill in LEGA_* and FANTABOT_LEAGUE_ID.' -ForegroundColor Yellow
    Write-Host 'Generate an encryption key with:' -ForegroundColor Yellow
    Write-Host '  .\.venv\Scripts\python.exe -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
}
$fantabot = Join-Path $repo '.venv\Scripts\fantabot.exe'
& $fantabot config-check

Write-Host ''
Write-Host 'Done. Activate the venv and go:' -ForegroundColor Green
Write-Host "  cd $repo"
Write-Host '  .\.venv\Scripts\Activate.ps1'
Write-Host '  fantabot --help'
Write-Host ''
Write-Host 'Then start Postgres and apply migrations:' -ForegroundColor Green
Write-Host '  docker compose up -d'
Write-Host '  fantabot db check'
