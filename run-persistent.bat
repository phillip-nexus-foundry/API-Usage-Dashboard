@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
PowerShell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%scripts\windows\start-dashboard.ps1" -OpenBrowser
