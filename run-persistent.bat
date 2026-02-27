@echo off
REM Persistent API Usage Dashboard — auto-restarts on crash
REM Usage: run-persistent.bat

echo ======================================
echo API Usage Dashboard (Persistent Mode)
echo ======================================
echo.

:loop
echo [%date% %time%] Starting dashboard server...
python app.py
echo.
echo [%date% %time%] Server exited with code %errorlevel%. Restarting in 5 seconds...
echo Press Ctrl+C to stop.
timeout /t 5 /nobreak >nul
goto loop
