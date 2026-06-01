#!/usr/bin/env bash
# DevFlow CI 会话启动脚本 — 规范 AI_COLLABORATION_GUIDE.md §7.1.1
# 用法: bash scripts/session_start.sh

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

TODAY=$(date +%Y%m%d)
EXISTING=$(ls sessions/ 2>/dev/null | grep "^${TODAY}-" | wc -l || echo 0)
SEQ=$(printf "%02d" $((EXISTING + 1)))
SESSION_ID="${TODAY}-${SEQ}"

mkdir -p "sessions/${SESSION_ID}"

echo "🧠 DevFlow CI 会话启动"
echo "  会话 ID: ${SESSION_ID}"
echo "  日期: $(date '+%Y-%m-%d %H:%M:%S')"
echo "  项目根: ${PROJECT_ROOT}"
echo ""
echo "📂 记忆文件状态:"

check_file() {
    local f="$1"
    if [ -f "$f" ]; then
        local lines
        lines=$(wc -l < "$f" | tr -d ' ')
        echo "  ✅ $f (${lines} 行)"
    else
        echo "  ❌ $f (缺失)"
    fi
}

check_file "AI_COLLABORATION_GUIDE.md"
check_file "COLLABORATION_PROMPT.md"
check_file "CURRENT_STATUS.md"
check_file "BUG_TRACKER.md"
check_file "docs/MEMORY_INDEX.md"
check_file "docs/ARCHITECTURE.md"
check_file "docs/CODING_STANDARDS.md"
check_file "docs/LEARNINGS.md"
check_file "docs/AGENT_PROMPT_TEMPLATES.md"
check_file "docs/decisions.md"
check_file "docs/success_cases.json"

# 检查根目录指针文件是否与 docs/ 分叉（简单时间戳对比）
echo ""
echo "🔍 指针文件分叉检查:"
for pair in "decisions.md:docs/decisions.md" "LEARNINGS.md:docs/LEARNINGS.md"; do
    root="${pair%%:*}"
    docs="${pair##*:}"
    if [ -f "$root" ] && [ -f "$docs" ]; then
        root_size=$(wc -c < "$root" | tr -d ' ')
        if [ "$root_size" -gt 200 ]; then
            echo "  ⚠️  $root 似乎包含完整内容（${root_size} bytes），应仅为指针文件"
        else
            echo "  ✅ $root → $docs (指针正常)"
        fi
    fi
done

# 查找上次会话
echo ""
echo "📋 上次会话:"
LAST_SESSION=$(ls sessions/ 2>/dev/null | sort -r | head -1 || echo "无")
if [ "$LAST_SESSION" != "无" ] && [ "$LAST_SESSION" != "$SESSION_ID" ]; then
    echo "  ${LAST_SESSION}"
    if [ -f "sessions/${LAST_SESSION}/summary.md" ]; then
        echo "  摘要: sessions/${LAST_SESSION}/summary.md"
    fi
else
    echo "  无（首次会话）"
fi

# 写入会话启动标记
cat > "sessions/${SESSION_ID}/started_at.txt" << EOF
session_id=${SESSION_ID}
started_at=$(date -Iseconds)
hostname=$(hostname 2>/dev/null || echo unknown)
EOF

echo ""
echo "✅ 会话目录已创建: sessions/${SESSION_ID}/"
echo ""
echo "💡 接下来："
echo "  1. 启动 AI 会话，AI 将按 AI_COLLABORATION_GUIDE.md §2.3 读取记忆文件"
echo "  2. 确认本次会话目标"
echo "  3. 会话结束时运行: bash scripts/session_end.sh ${SESSION_ID}"
