@echo off
setlocal EnableExtensions EnableDelayedExpansion

if /I "%~1"=="monitor" goto monitor

set "PORT=8050"
set "APP_FILE=app.py"
set "LOGFILE=%~dp0server-persistent.log"

call :log "---- Run requested ----"
call :log "Checking for existing listener on port %PORT%."

set "FOUND_PID="
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:":%PORT% .*LISTENING"') do (
    set "FOUND_PID=%%P"
    call :log "Port %PORT% in use by PID !FOUND_PID!. Stopping existing process."
    taskkill /F /PID !FOUND_PID! >nul 2>&1
    if !errorlevel! equ 0 (
        call :log "Stopped PID !FOUND_PID! on port %PORT%."
    ) else (
        call :log "Failed to stop PID !FOUND_PID! (may have already exited)."
    )
)

call :log "Starting %APP_FILE% with pythonw.exe in background."
start "" /b pythonw.exe "%APP_FILE%" >nul 2>&1

set "START_OK="
set "SERVER_PID="
for /l %%I in (1,1,20) do (
    for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:":%PORT% .*LISTENING"') do (
        set "SERVER_PID=%%P"
        set "START_OK=1"
    )
    if defined START_OK goto started
    timeout /t 1 /nobreak >nul
)

call :log "ERROR: Server did not bind to port %PORT% within timeout."
exit /b 1

:started
call :log "Started server process. PID=%SERVER_PID%."
call :log "Server is listening on port %PORT%. Startup successful."

powershell -NoProfile -Command "Add-Type -AssemblyName PresentationFramework; [System.Windows.MessageBox]::Show('API dashboard server started on port %PORT%.','Server Started','OK','Information') | Out-Null"

start "" /b cmd /c ""%~f0" monitor %SERVER_PID% "%LOGFILE%""
exit /b 0

:monitor
setlocal EnableExtensions EnableDelayedExpansion
set "MON_PID=%~2"
set "MON_LOG=%~3"
if not defined MON_PID exit /b 0
if not defined MON_LOG set "MON_LOG=%~dp0server-persistent.log"

:wait_loop
tasklist /FI "PID eq %MON_PID%" 2>nul | findstr /R /C:"\<%MON_PID%\>" >nul
if %errorlevel% equ 0 (
    timeout /t 5 /nobreak >nul
    goto wait_loop
)

for /f %%T in ('powershell -NoProfile -Command "Get-Date -Format ''yyyy-MM-dd HH:mm:ss''"') do set "NOW=%%T"
>> "%MON_LOG%" echo [!NOW!] Server stopped. PID=%MON_PID%.
exit /b 0

:log
for /f %%T in ('powershell -NoProfile -Command "Get-Date -Format ''yyyy-MM-dd HH:mm:ss''"') do set "NOW=%%T"
>> "%LOGFILE%" echo [!NOW!] %~1
exit /b 0
