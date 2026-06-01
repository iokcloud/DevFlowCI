@echo off
REM 安装 git pre-commit 钩子 — 用法: scripts\install_hooks.bat
REM 提交前自动运行 scripts\pre_commit_check.bat

chcp 65001 >nul
cd /d "%~dp0\.."

if not exist ".git" (
    echo 错误: 当前目录不是 git 仓库
    exit /b 1
)

(
echo @echo off
echo chcp 65001 ^>nul
echo cd /d "%%~dp0\.."
echo call scripts\pre_commit_check.bat
echo if errorlevel 1 exit /b 1
) > .git\hooks\pre-commit.bat

(
echo @echo off
echo call "%%~dp0pre-commit.bat"
) > .git\hooks\pre-commit

echo.
echo 已安装 pre-commit 钩子: .git\hooks\pre-commit
echo 提交前将运行 scripts\pre_commit_check.bat
echo.
exit /b 0
