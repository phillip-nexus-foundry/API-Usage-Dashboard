$ErrorActionPreference = "Stop"

$RepoRoot = "\\wsl.localhost\Ubuntu-24.04\home\agents\openclaw-local\core\projects\API-Useage-Dashboard"
$LocalScriptRoot = Join-Path $env:USERPROFILE "Scripts"
$Desktop = [Environment]::GetFolderPath("Desktop")
$StartupFolder = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Startup"
$TaskName = "OpenClaw API Usage Dashboard"

New-Item -ItemType Directory -Force -Path $LocalScriptRoot | Out-Null

$StartSource = Join-Path $RepoRoot "scripts\windows\start-dashboard.ps1"
$StopSource = Join-Path $RepoRoot "scripts\windows\stop-dashboard.ps1"
$StartTarget = Join-Path $LocalScriptRoot "start-api-usage-dashboard.ps1"
$StopTarget = Join-Path $LocalScriptRoot "stop-api-usage-dashboard.ps1"
$OpenCmd = Join-Path $Desktop "Open API Usage Dashboard.cmd"
$StopCmd = Join-Path $Desktop "Stop API Usage Dashboard.cmd"
$StartupCmd = Join-Path $StartupFolder "Start API Usage Dashboard.cmd"

Copy-Item $StartSource $StartTarget -Force
Copy-Item $StopSource $StopTarget -Force

$openCmdContent = "@echo off`r`nPowerShell -NoProfile -ExecutionPolicy Bypass -File `"$StartTarget`" -OpenBrowser`r`n"
$stopCmdContent = "@echo off`r`nPowerShell -NoProfile -ExecutionPolicy Bypass -File `"$StopTarget`"`r`n"
$startupCmdContent = "@echo off`r`nPowerShell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$StartTarget`"`r`n"
Set-Content -Path $OpenCmd -Value $openCmdContent -Encoding ASCII
Set-Content -Path $StopCmd -Value $stopCmdContent -Encoding ASCII
Set-Content -Path $StartupCmd -Value $startupCmdContent -Encoding ASCII

try {
    $action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$StartTarget`""
    $trigger = New-ScheduledTaskTrigger -AtLogOn
    $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $action `
        -Trigger $trigger `
        -Principal $principal `
        -Description "Starts the OpenClaw API Usage Dashboard in the background at logon." `
        -Force | Out-Null

    if (Test-Path $StartupCmd) {
        Remove-Item $StartupCmd -Force
    }
}
catch {
    Write-Warning "Scheduled task registration failed; keeping Startup-folder fallback. $($_.Exception.Message)"
}
