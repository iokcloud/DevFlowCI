@echo off
REM DevFlow CI 会话结束检查 (Windows)
REM 用法: scripts\session_end.bat [SESSION_ID]

setlocal
cd /d "%~dp0\.."

set "SESSION_ID=%~1"
if "%SESSION_ID%"=="" (
    for /f "delims=" %%i in ('dir /b /o-n sessions 2^>nul ^| findstr /r "^[0-9]"') do (
        set "SESSION_ID=%%i"
        goto :found
    )
)
:found

if "%SESSION_ID%"=="" (
    echo 🚫 错误：未找到会话 ID
    exit /b 1
)

echo 📋 会话质量检查 — %SESSION_ID%
echo.

set "SCORE=0"

if exist "sessions\%SESSION_ID%\summary.md" (
    echo   ✅ sessions\%SESSION_ID%\summary.md 已存在
    set /a SCORE+=30
) else (
    echo   ❌ sessions\%SESSION_ID%\summary.md 缺失
)

findstr /c:"%SESSION_ID%" CURRENT_STATUS.md >nul 2>&1
if %errorlevel%==0 (
    echo   ✅ CURRENT_STATUS.md 已引用本次会话
    set /a SCORE+=30
) else (
    echo   ⚠️  CURRENT_STATUS.md 未引用 %SESSION_ID%
    set /a SCORE+=10
)

if exist ".git" (
    echo   ✅ Git 仓库已初始化
    set /a SCORE+=10
) else (
    echo   ℹ️  Git 仓库未初始化
)

echo.
echo ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
echo   会话质量分: %SCORE% / 100
echo ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

endlocal
exit /b 0
