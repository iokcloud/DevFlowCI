@echo off
REM Install git pre-commit hook. Usage: scripts\install_hooks.bat

cd /d "%~dp0\.."

if not exist ".git" (
    echo ERROR: not a git repository
    exit /b 1
)

copy /Y "scripts\pre-commit.hook" ".git\hooks\pre-commit" >nul

echo.
echo pre-commit hook installed: .git/hooks/pre-commit
echo Runs: py_compile + pytest tests/
echo.
exit /b 0
