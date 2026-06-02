@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul 2>&1
title DevFlow CI

set "ROOT=%~dp0"
cd /d "%ROOT%"

echo.
echo ========================================
echo   DevFlow CI
echo ========================================
echo.

set "PY=%ROOT%venv\Scripts\python.exe"
set "PORT=8000"

if not exist "%ROOT%.env" (
    if exist "%ROOT%.env.example" (
        echo [info] Creating .env from .env.example
        copy /y "%ROOT%.env.example" "%ROOT%.env" >nul
        start notepad "%ROOT%.env"
        echo [info] Please set DEEPSEEK_API_KEY in .env
        echo.
    ) else (
        echo [warn] No .env file found
        echo.
    )
)

if not exist "%PY%" (
    echo [step] Creating virtual environment...
    where python >nul 2>&1
    if errorlevel 1 (
        echo [error] Python not found. Install Python 3.10+ and add to PATH.
        goto fail
    )
    python -m venv "%ROOT%venv"
    if errorlevel 1 (
        echo [error] Failed to create venv
        goto fail
    )
)

"%PY%" -c "import fastapi, uvicorn" >nul 2>&1
if errorlevel 1 (
    echo [step] Installing dependencies...
    "%PY%" -m pip install -r "%ROOT%requirements.txt"
    if errorlevel 1 (
        echo [error] pip install failed
        goto fail
    )
)

echo [step] Checking port %PORT% ...
powershell -NoProfile -ExecutionPolicy Bypass -File "%ROOT%scripts\stop_server.ps1" -Port %PORT% | findstr /v "^$"

echo.
echo [url]  http://127.0.0.1:%PORT%
echo [docs] http://127.0.0.1:%PORT%/docs
echo [stop] Press Ctrl+C in this window
echo.

start /b cmd /c "ping 127.0.0.1 -n 3 >nul && start http://127.0.0.1:%PORT%/"

"%PY%" -m uvicorn main:app --host 127.0.0.1 --port %PORT% --reload
set "EXIT_CODE=!ERRORLEVEL!"

if not "!EXIT_CODE!"=="0" (
    echo.
    echo [error] Server exited with code !EXIT_CODE!
    echo         Run scripts\stop_server.bat if port %PORT% is still busy.
)
pause
exit /b !EXIT_CODE!

:fail
echo.
pause
exit /b 1
