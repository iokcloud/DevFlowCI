@echo off
REM DevFlow CI 停止服务 — 用法: scripts\stop_server.bat
REM 终止所有 uvicorn main:app 进程（含 --reload 孤儿 worker）

chcp 65001 >nul
cd /d "%~dp0\.."

echo.
echo 正在查找占用 8000 端口的进程...
echo.

set FOUND=0

for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8000" ^| findstr "LISTENING"') do (
    echo 终止 PID %%a ...
    taskkill /F /PID %%a >nul 2>&1
    set FOUND=1
)

REM 兜底：按命令行匹配 uvicorn
for /f "tokens=2 delims=," %%a in ('wmic process where "CommandLine like '%%uvicorn main:app%%'" get ProcessId /format:csv 2^>nul ^| findstr /r "[0-9]"') do (
    echo 终止 uvicorn PID %%a ...
    taskkill /F /PID %%a >nul 2>&1
    set FOUND=1
)

timeout /t 2 /nobreak >nul

netstat -ano | findstr ":8000" | findstr "LISTENING" >nul 2>&1
if errorlevel 1 (
    echo.
    echo 端口 8000 已释放。
) else (
    echo.
    echo 警告: 8000 仍被占用，请手动检查:
    netstat -ano | findstr ":8000"
)

echo.
exit /b 0
