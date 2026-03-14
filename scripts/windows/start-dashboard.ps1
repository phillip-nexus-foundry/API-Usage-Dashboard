param(
    [switch]$OpenBrowser
)

$ErrorActionPreference = "Stop"

$RepoRoot = "\\wsl.localhost\Ubuntu-24.04\home\agents\openclaw-local\core\projects\API-Useage-Dashboard"
$PythonExe = Join-Path $env:USERPROFILE ".venvs\api-usage-dashboard\Scripts\python.exe"
$LocalRoot = Join-Path $env:LOCALAPPDATA "OpenClaw\api-usage-dashboard"
$LogRoot = Join-Path $LocalRoot "logs"
$DataRoot = Join-Path $LocalRoot "data"
$ProfilesRoot = Join-Path $LocalRoot "browser_profiles"
$SessionsDir = "\\wsl.localhost\Ubuntu-24.04\home\agents\openclaw-local\core\agents\main\sessions"
$DashboardUrl = "http://127.0.0.1:8050/"

New-Item -ItemType Directory -Force -Path $LogRoot | Out-Null
New-Item -ItemType Directory -Force -Path $DataRoot | Out-Null
New-Item -ItemType Directory -Force -Path $ProfilesRoot | Out-Null

$stdoutLog = Join-Path $LogRoot "dashboard.stdout.log"
$stderrLog = Join-Path $LogRoot "dashboard.stderr.log"
$sqlitePath = (Join-Path $DataRoot "dashboard.db").Replace("\", "/")
$env:DATABASE_URL = "sqlite:///$sqlitePath"
$env:SESSIONS_DIR = $SessionsDir
$env:OPENCLAW_WSL_DISTRO = "Ubuntu-24.04"
$env:BRAVE_CDP_HOST = "127.0.0.1"
$env:DASHBOARD_DATA_DIR = $DataRoot
$env:DASHBOARD_BROWSER_PROFILES_DIR = $ProfilesRoot

$listener = Get-NetTCPConnection -LocalPort 8050 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $listener) {
    if (-not (Test-Path $PythonExe)) {
        throw "Dashboard venv python not found at $PythonExe"
    }

    Start-Process `
        -FilePath $PythonExe `
        -ArgumentList "run.py" `
        -WorkingDirectory $RepoRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput $stdoutLog `
        -RedirectStandardError $stderrLog | Out-Null

    for ($i = 0; $i -lt 20; $i++) {
        Start-Sleep -Milliseconds 500
        $listener = Get-NetTCPConnection -LocalPort 8050 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($listener) {
            break
        }
    }
}

if ($OpenBrowser) {
    Start-Process $DashboardUrl | Out-Null
}
