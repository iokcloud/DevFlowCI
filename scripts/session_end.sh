#!/usr/bin/env bash
# DevFlow CI 会话结束辅助脚本 — 规范 AI_COLLABORATION_GUIDE.md §3.3 / §7.1.3
# 用法: bash scripts/session_end.sh [SESSION_ID]
# 注意：本脚本仅做完整性检查，summary.md 和 CURRENT_STATUS.md 需由 AI 写入

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

SESSION_ID="${1:-}"
if [ -z "$SESSION_ID" ]; then
    SESSION_ID=$(ls sessions/ 2>/dev/null | sort -r | head -1 || echo "")
fi

if [ -z "$SESSION_ID" ]; then
    echo "🚫 错误：未找到会话 ID。用法: bash scripts/session_end.sh YYYYMMDD-NN"
    exit 1
fi

SESSION_DIR="sessions/${SESSION_ID}"
SCORE=0
MAX=100
ISSUES=()

echo "📋 会话质量检查 — ${SESSION_ID}"
echo ""

# P0: session summary
if [ -f "${SESSION_DIR}/summary.md" ]; then
    echo "  ✅ sessions/${SESSION_ID}/summary.md 已存在"
    SCORE=$((SCORE + 30))
else
    echo "  ❌ sessions/${SESSION_ID}/summary.md 缺失（P0 必做）"
    ISSUES+=("写入 sessions/${SESSION_ID}/summary.md")
fi

# P0: CURRENT_STATUS updated today or in session
if [ -f "CURRENT_STATUS.md" ]; then
    if grep -q "${SESSION_ID}" CURRENT_STATUS.md 2>/dev/null; then
        echo "  ✅ CURRENT_STATUS.md 已引用本次会话"
        SCORE=$((SCORE + 30))
    else
        echo "  ⚠️  CURRENT_STATUS.md 未引用 ${SESSION_ID}"
        ISSUES+=("更新 CURRENT_STATUS.md 并标注会话 ID")
        SCORE=$((SCORE + 10))
    fi
else
    echo "  ❌ CURRENT_STATUS.md 缺失"
    ISSUES+=("创建/恢复 CURRENT_STATUS.md")
fi

# P1: started_at marker
if [ -f "${SESSION_DIR}/started_at.txt" ]; then
    echo "  ✅ 会话启动标记存在"
    SCORE=$((SCORE + 10))
else
    echo "  ⚠️  无启动标记（可能未运行 session_start.sh）"
fi

# P1: git status (optional)
if [ -d ".git" ]; then
    echo "  ✅ Git 仓库已初始化"
    SCORE=$((SCORE + 10))
else
    echo "  ℹ️  Git 仓库未初始化"
fi

# P2: pointer file health
POINTER_OK=true
for f in decisions.md LEARNINGS.md AGENT_PROMPT_TEMPLATES.md; do
    if [ -f "$f" ]; then
        size=$(wc -c < "$f" | tr -d ' ')
        if [ "$size" -gt 200 ]; then
            echo "  ⚠️  $f 不是指针文件（${size} bytes）"
            POINTER_OK=false
        fi
    fi
done
if [ "$POINTER_OK" = true ]; then
    echo "  ✅ 根目录指针文件正常"
    SCORE=$((SCORE + 20))
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  会话质量分: ${SCORE} / ${MAX}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

if [ ${#ISSUES[@]} -gt 0 ]; then
    echo ""
    echo "待完成项:"
    for item in "${ISSUES[@]}"; do
        echo "  - [ ] ${item}"
    done
    exit 1
fi

echo ""
echo "✅ 会话结束检查通过"
exit 0
