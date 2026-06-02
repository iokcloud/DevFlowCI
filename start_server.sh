#!/usr/bin/env bash
# 启动 DevFlow CI 开发服务器（Git Bash）
cd "$(dirname "$0")"
source venv/Scripts/activate
# 使用 main.py 内置配置（排除 deliveries 等工作流输出目录的热重载）
python main.py
