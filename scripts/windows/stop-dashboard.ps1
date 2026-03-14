$ErrorActionPreference = "Stop"

$listener = Get-NetTCPConnection -LocalPort 8050 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($listener) {
    Stop-Process -Id $listener.OwningProcess -Force
}

Get-CimInstance Win32_Process |
    Where-Object { $_.Name -eq "python.exe" -and $_.CommandLine -match "API-Useage-Dashboard" -and $_.CommandLine -match "run.py" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
