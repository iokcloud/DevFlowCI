@echo off
REM DevFlow CI 会话启动脚本 (Windows) — 规范 AI_COLLABORATION_GUIDE.md §7.1.1
REM 用法: scripts\session_start.bat

setlocal enabledelayedexpansion

cd /d "%~dp0\.."
set "PROJECT_ROOT=%CD%"

for /f "tokens=1-3 delims=/ " %%a in ('date /t') do set "TODAY_RAW=%%c%%a%%b"
REM 简化：使用 PowerShell 获取日期
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd"') do set "TODAY=%%i"

set "COUNT=0"
if exist "sessions\" (
    for /d %%d in (sessions\%TODAY%-*) do set /a COUNT+=1
)
set /a NEXT=COUNT+1
if %NEXT% LSS 10 (set "SEQ=0%NEXT%") else (set "SEQ=%NEXT%")
set "SESSION_ID=%TODAY%-%SEQ%"

if not exist "sessions\%SESSION_ID%" mkdir "sessions\%SESSION_ID%"

echo.
echo 🧠 DevFlow CI 会话启动
echo   会话 ID: %SESSION_ID%
echo   项目根: %PROJECT_ROOT%
echo.
echo 📂 记忆文件状态:

call :check_file "AI_COLLABORATION_GUIDE.md"
call :check_file "COLLABORATION_PROMPT.md"
call :check_file "CURRENT_STATUS.md"
call :check_file "BUG_TRACKER.md"
call :check_file "docs\MEMORY_INDEX.md"
call :check_file "docs\ARCHITECTURE.md"
call :check_file "docs\CODING_STANDARDS.md"
call :check_file "docs\LEARNINGS.md"
call :check_file "docs\AGENT_PROMPT_TEMPLATES.md"
call :check_file "docs\decisions.md"
call :check_file "docs\success_cases.json"

echo.
echo ✅ 会话目录已创建: sessions\%SESSION_ID%\
echo.
echo 💡 接下来：启动 AI 会话并确认本次目标
echo    会话结束时运行: scripts\session_end.bat %SESSION_ID%

echo session_id=%SESSION_ID%> "sessions\%SESSION_ID%\started_at.txt"
echo started_at=%DATE% %TIME%>> "sessions\%SESSION_ID%\started_at.txt"

endlocal
exit /b 0

:check_file
if exist "%~1" (
    for %%A in ("%~1") do echo   ✅ %~1 (%%~zA bytes)
) else (
    echo   ❌ %~1 (缺失)
)
exit /b 0
