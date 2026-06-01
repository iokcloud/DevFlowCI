@echo off
setlocal
cd /d "%~dp0.."

if "%VIRTUAL_ENV%"=="" (
    if exist "venv\Scripts\python.exe" (
        set "PY=venv\Scripts\python.exe"
    ) else (
        set "PY=python"
    )
) else (
    set "PY=python"
)

if "%~1"=="" (
    echo [analyze_blocked] 分析 deliveries 下最新项目...
    "%PY%" scripts\analyze_blocked.py --latest
) else if /I "%~1"=="--latest" (
    "%PY%" scripts\analyze_blocked.py --latest
) else (
    echo [analyze_blocked] 项目 ID: %~1
    "%PY%" scripts\analyze_blocked.py %*
)

set EXIT_CODE=%ERRORLEVEL%
echo.
if %EXIT_CODE% NEQ 0 (
    echo 分析失败 ^(exit %EXIT_CODE%^). 请确认服务已启动: python main.py
) else (
    echo 完成.
)
pause
exit /b %EXIT_CODE%
