@echo off
REM API Usage Dashboard Launcher
REM Auto-installs dependencies and starts the server

echo ======================================
echo API Usage Dashboard
echo ======================================

REM Check if Python is installed
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: Python not found. Please install Python 3.9+
    pause
    exit /b 1
)

REM Install dependencies if needed
echo.
echo Checking dependencies...
pip install -q -r requirements.txt
if %errorlevel% neq 0 (
    echo ERROR: Failed to install dependencies
    pause
    exit /b 1
)

echo Dependencies installed.
echo.
echo Starting server on http://127.0.0.1:8050
echo.
echo Ctrl+C to stop.
echo.

REM Start the server
python app.py
