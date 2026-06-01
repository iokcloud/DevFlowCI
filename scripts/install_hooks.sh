#!/usr/bin/env bash
# 安装 git pre-commit 钩子 — 用法: bash scripts/install_hooks.sh

set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -d .git ]; then
    echo "错误: 当前目录不是 git 仓库"
    exit 1
fi

cat > .git/hooks/pre-commit << 'EOF'
#!/bin/sh
cd "$(git rev-parse --show-toplevel)" || exit 1
bash scripts/pre_commit_check.sh
EOF

chmod +x .git/hooks/pre-commit

echo ""
echo "已安装 pre-commit 钩子: .git/hooks/pre-commit"
echo "提交前将运行 scripts/pre_commit_check.sh"
echo ""
