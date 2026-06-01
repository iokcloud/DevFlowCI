@echo off
REM DevFlow CI pre-commit check (Windows CMD). Usage: scripts\pre_commit_check.bat

cd /d "%~dp0\.."
set FAILED=0

echo.
echo DevFlow CI pre-commit check
echo.

if exist venv\Scripts\python.exe (
    set PY=venv\Scripts\python.exe
) else (
    set PY=python
)

echo [py_compile main]
%PY% -m py_compile main.py config.py
if errorlevel 1 set FAILED=1

echo [py_compile workflow]
%PY% -m py_compile workflow\executor.py workflow\auto_fix.py workflow\error_logger.py
if errorlevel 1 set FAILED=1

echo [py_compile core]
%PY% -m py_compile memory\case_store.py database\models.py
if errorlevel 1 set FAILED=1

echo [pytest]
%PY% -m pytest tests/ -q --tb=no
if errorlevel 1 set FAILED=1

echo.
if "%FAILED%"=="1" (
    echo CHECK FAILED
    exit /b 1
)
echo ALL CHECKS PASSED
exit /b 0
