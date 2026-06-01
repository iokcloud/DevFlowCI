@echo off
REM DevFlow CI 冒烟测试 — 用法: scripts\test_flow.bat
REM 前置: 另开终端已运行 start.bat 或 python main.py

chcp 65001 >nul
cd /d "%~dp0\.."

if not exist "venv\Scripts\activate.bat" (
    echo 错误: 未找到 venv，请先 python -m venv venv
    exit /b 1
)

call venv\Scripts\activate.bat
python test_flow.py
exit /b %ERRORLEVEL%
