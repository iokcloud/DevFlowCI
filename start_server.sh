#!/usr/bin/env bash
# 启动 DevFlow CI 开发服务器（Git Bash）
cd "$(dirname "$0")"
source venv/Scripts/activate
python main.py
