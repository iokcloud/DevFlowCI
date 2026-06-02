@echo off
REM DevFlow CI — refresh HANDOFF before a new Cursor chat
REM Usage:
REM   scripts\session_checkpoint.bat
REM   scripts\session_checkpoint.bat iterate report_dashboard

setlocal
cd /d "%~dp0\.."

if "%~1"=="" (
    python "%~dp0session_checkpoint.py"
) else (
    python "%~dp0session_checkpoint.py" --next-goal "%*"
)

endlocal
