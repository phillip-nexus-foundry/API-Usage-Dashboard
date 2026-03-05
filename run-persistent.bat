@echo off
setlocal EnableExtensions EnableDelayedExpansion

if /I "%~1"=="monitor" goto monitor

set "PORT=8050"
set "APP_FILE=app.py"
set "BASEDIR=%~dp0"
set "RUNNER_LOG=%BASEDIR%server-runner.log"
set "SERVER_OUT=%BASEDIR%server-persistent.log"
set "SERVER_ERR=%BASEDIR%server-persistent.err"
set "MAX_LOG_BYTES=10485760"

call :rotate_log "%RUNNER_LOG%"
call :rotate_log "%SERVER_OUT%"
call :rotate_log "%SERVER_ERR%"

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
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:":%PORT% .*LISTENING"') do (
    call :log "ERROR: Port %PORT% still in use by PID %%P. Aborting launch to avoid duplicate server."
    exit /b 1
)

call :log "Starting %APP_FILE% in hidden background process."
set "SERVER_PID="
for /f %%P in ('powershell -NoProfile -Command "$p=Start-Process -FilePath python -ArgumentList '!APP_FILE!' -WorkingDirectory '!BASEDIR!' -WindowStyle Hidden -RedirectStandardOutput '!SERVER_OUT!' -RedirectStandardError '!SERVER_ERR!' -PassThru; $p.Id"') do (
    set "SERVER_PID=%%P"
)
if not defined SERVER_PID (
    call :log "ERROR: Failed to launch hidden python process."
    exit /b 1
)

set "START_OK="
for /l %%I in (1,1,20) do (
    netstat -ano | findstr /R /C:":%PORT% .*LISTENING" >nul 2>&1 && set "START_OK=1"
    if defined START_OK goto started
    timeout /t 1 /nobreak >nul
)

call :log "ERROR: Server did not bind to port %PORT% within timeout."
exit /b 1

:started
call :log "Started server process. PID=%SERVER_PID%."
call :log "Server is listening on port %PORT%. Startup successful."

start "" /b cmd /c ""%~f0" monitor %SERVER_PID% "%RUNNER_LOG%""
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

for /f "delims=" %%T in ('powershell -NoProfile -Command "Get-Date -Format 'yyyy-MM-dd HH:mm:ss'"') do set "NOW=%%T"
>> "%MON_LOG%" echo [!NOW!] Server stopped. PID=%MON_PID%.
exit /b 0

:log
for /f "delims=" %%T in ('powershell -NoProfile -Command "Get-Date -Format 'yyyy-MM-dd HH:mm:ss'"') do set "NOW=%%T"
>> "%RUNNER_LOG%" echo [!NOW!] %~1
exit /b 0

:rotate_log
set "TARGET=%~1"
if not defined TARGET exit /b 0
if not exist "%TARGET%" exit /b 0
for %%A in ("%TARGET%") do set "LOGSIZE=%%~zA"
if not defined LOGSIZE exit /b 0
if !LOGSIZE! LSS %MAX_LOG_BYTES% exit /b 0
for /f %%T in ('powershell -NoProfile -Command "Get-Date -Format 'yyyyMMdd-HHmmss'"') do set "STAMP=%%T"
set "ARCHIVE=%TARGET%.!STAMP!.old"
move /Y "%TARGET%" "!ARCHIVE!" >nul 2>&1
type nul > "%TARGET%"
exit /b 0
