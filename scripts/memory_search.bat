@echo off
REM DevFlow CI 记忆检索 — 用法: scripts\memory_search.bat "关键词"

chcp 65001 >nul
cd /d "%~dp0\.."

if not exist "venv\Scripts\python.exe" (
    python scripts\memory_search.py %*
    exit /b %ERRORLEVEL%
)

venv\Scripts\python.exe scripts\memory_search.py %*
exit /b %ERRORLEVEL%
