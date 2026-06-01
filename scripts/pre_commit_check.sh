#!/usr/bin/env bash
# DevFlow CI 提交前自检 — AI_COLLABORATION_GUIDE.md §7.2 / §4.1
# 用法: bash scripts/pre_commit_check.sh

set -euo pipefail
cd "$(dirname "$0")/.."
FAILED=0

echo "🔍 DevFlow CI pre-commit 自检"
echo ""

check() {
    local name="$1"
    shift
    echo -n "  [$name] "
    if "$@" >/dev/null 2>&1; then
        echo "✅"
    else
        echo "❌"
        FAILED=1
    fi
}

# 🔴 阻断级
echo "阻断级:"
check "py_compile main" python -m py_compile main.py config.py
check "py_compile workflow" python -m py_compile workflow/executor.py workflow/auto_fix.py workflow/error_logger.py
check "py_compile core" python -m py_compile memory/case_store.py database/models.py
check "pytest" python -m pytest tests/ -q --tb=no
check "密钥扫描" bash -c '! rg -l "(api.?key|secret|password)\s*=\s*[\"'\''][^\"'\'']{20,}" --glob "!*.example" --glob "!.env*" . 2>/dev/null | head -1 | grep -q .'
check "merge冲突" bash -c '! rg "<<<<<<<|>>>>>>>|=======" . 2>/dev/null | head -1 | grep -q .'

# 🟡 指针文件健康
echo ""
echo "记忆文件:"
for f in decisions.md LEARNINGS.md AGENT_PROMPT_TEMPLATES.md; do
    if [ -f "$f" ]; then
        size=$(wc -c < "$f" | tr -d ' ')
        if [ "$size" -gt 300 ]; then
            echo "  ⚠️  $f 不是指针文件 (${size} bytes)，应编辑 docs/ 下权威源"
            FAILED=1
        else
            echo "  ✅ $f → docs/"
        fi
    fi
done

echo ""
if [ "$FAILED" -eq 0 ]; then
    echo "✅ 所有阻断级检查通过"
    exit 0
else
    echo "🚫 检查未通过，请修复后再提交"
    exit 1
fi
