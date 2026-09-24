# The unattended matchday run: bring Postgres up, then field every lega with an open matchday.
#
# Scheduled by Windows Task Scheduler (see `scripts/register_matchday_task.ps1`). Idempotent:
# a run with no open matchday, or past the first kickoff, changes nothing, so it is safe to
# run several times a day. It submits only when BOTH locks are set: FANTABOT_AUTO_ACT=true in
# .env (the operator's choice, never this script's) and the --arm below.
#
# Output goes to logs\matchday-<date>.log, appended per run. Exit code is submit-all's.

$ErrorActionPreference = "Continue"
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

$logDir = Join-Path $repo "logs"
New-Item -ItemType Directory -Force $logDir | Out-Null
$log = Join-Path $logDir ("matchday-{0:yyyy-MM-dd}.log" -f (Get-Date))

function Write-Log([string]$line) {
    ("{0:yyyy-MM-dd HH:mm:ss} {1}" -f (Get-Date), $line) | Out-File -FilePath $log -Append -Encoding utf8
}

$conda = Join-Path $env:USERPROFILE "miniconda3\Scripts\conda.exe"
$dockerDesktop = Join-Path $env:LOCALAPPDATA "Programs\DockerDesktop\Docker Desktop.exe"

Write-Log "---- matchday run"

# Docker Desktop is not a service: start it if the engine does not answer, then wait.
docker info *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Log "docker engine down; starting Docker Desktop"
    if (Test-Path $dockerDesktop) { Start-Process $dockerDesktop }
    for ($i = 0; $i -lt 36; $i++) {
        Start-Sleep -Seconds 5
        docker info *> $null
        if ($LASTEXITCODE -eq 0) { break }
    }
    if ($LASTEXITCODE -ne 0) { Write-Log "docker never came up; giving up"; exit 1 }
}

# Native output goes through cmd's redirection: PowerShell 5.1's `>>` would re-encode it as
# UTF-16 into a file that Write-Log keeps in UTF-8.
cmd /c "docker compose up -d --wait >> `"$log`" 2>&1"
if ($LASTEXITCODE -ne 0) { Write-Log "docker compose up failed"; exit 1 }

# Rich writes box-drawing characters; without UTF-8 the log fills with mojibake.
$env:PYTHONIOENCODING = "utf-8"
$env:COLUMNS = "160"

cmd /c "`"$conda`" run -n fanta --no-capture-output fantabot lineup submit-all --arm >> `"$log`" 2>&1"
$code = $LASTEXITCODE
Write-Log "submit-all exited $code"
exit $code
