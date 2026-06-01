@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo.
echo ╔══════════════════════════════════════╗
echo ║     🤖 DevFlow CI 启动中...          ║
echo ╚══════════════════════════════════════╝
echo.

REM 检查 .env 文件
if not exist ".env" (
    echo ⚠️  未找到 .env 文件，正在从 .env.example 复制...
    copy .env.example .env >nul
    echo ⚠️  请编辑 .env 文件填入 DEEPSEEK_API_KEY 后重新启动
    start notepad .env
    pause
    exit /b 1
)

REM 检查虚拟环境
if not exist "venv\Scripts\activate.bat" (
    echo 🚫 错误：未找到虚拟环境，请先运行: python -m venv venv
    pause
    exit /b 1
)

REM 激活虚拟环境
call venv\Scripts\activate.bat

REM 检查依赖
python -c "import fastapi" 2>nul
if errorlevel 1 (
    echo 📦 正在安装依赖...
    pip install -r requirements.txt -q
)

echo.
echo 🚀 启动服务: http://localhost:8000
echo 📋 API 文档: http://localhost:8000/docs
echo ⏹  按 Ctrl+C 停止服务
echo.

REM 等待一秒后自动打开浏览器
start "" http://localhost:8000

REM 启动服务器
python -m uvicorn main:app --host 127.0.0.1 --port 8000 --reload

pause
