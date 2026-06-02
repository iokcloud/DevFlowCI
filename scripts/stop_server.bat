@echo off
setlocal EnableExtensions
chcp 65001 >nul 2>&1
title DevFlow CI - Stop

cd /d "%~dp0\.."

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop_server.ps1" -Port 8000

echo.
pause
exit /b 0
