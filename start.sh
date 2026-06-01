#!/bin/bash
# DevFlow CI 启动脚本 (Git Bash)
# 用法: bash start.sh  或 双击后在 Git Bash 中运行

set -e
cd "$(dirname "$0")"

echo ""
echo "╔══════════════════════════════════════╗"
echo "║     🤖 DevFlow CI 启动中...          ║"
echo "╚══════════════════════════════════════╝"
echo ""

# 检查 .env
if [ ! -f ".env" ]; then
    echo "⚠️  未找到 .env 文件，正在从 .env.example 复制..."
    cp .env.example .env
    echo "⚠️  请编辑 .env 文件填入 DEEPSEEK_API_KEY 后重新启动"
    exit 1
fi

# 检查虚拟环境
if [ ! -f "venv/Scripts/activate" ]; then
    echo "🚫 错误：未找到虚拟环境，请先运行: python -m venv venv"
    exit 1
fi

# 激活虚拟环境
source venv/Scripts/activate

# 检查依赖
python -c "import fastapi" 2>/dev/null || {
    echo "📦 正在安装依赖..."
    pip install -r requirements.txt -q
}

echo ""
echo "🚀 启动服务: http://localhost:8000"
echo "📋 API 文档: http://localhost:8000/docs"
echo "⏹  按 Ctrl+C 停止服务"
echo ""

# 打开浏览器
sleep 1 && start "" "http://localhost:8000" 2>/dev/null &

# 启动服务器
python -m uvicorn main:app --host 127.0.0.1 --port 8000 --reload
