@echo off
REM DevFlow CI 提交前自检（Windows）— 用法: scripts\pre_commit_check.bat

chcp 65001 >nul
cd /d "%~dp0\.."
set FAILED=0

echo.
echo DevFlow CI pre-commit 自检
echo.

echo 阻断级:

python -m py_compile main.py config.py >nul 2>&1
if errorlevel 1 (echo   [py_compile main] FAIL & set FAILED=1) else (echo   [py_compile main] OK)

python -m py_compile workflow\executor.py workflow\auto_fix.py workflow\error_logger.py >nul 2>&1
if errorlevel 1 (echo   [py_compile workflow] FAIL & set FAILED=1) else (echo   [py_compile workflow] OK)

python -m py_compile memory\case_store.py database\models.py >nul 2>&1
if errorlevel 1 (echo   [py_compile core] FAIL & set FAILED=1) else (echo   [py_compile core] OK)

if exist venv\Scripts\activate.bat (
    call venv\Scripts\activate.bat
    pytest tests/ -q --tb=no >nul 2>&1
    if errorlevel 1 (echo   [pytest] FAIL & set FAILED=1) else (echo   [pytest] OK)
) else (
    echo   [pytest] SKIP ^(no venv^)
)

echo.
if "%FAILED%"=="1" (
    echo 检查未通过，请修复后再提交
    exit /b 1
)
echo 所有检查通过
exit /b 0
