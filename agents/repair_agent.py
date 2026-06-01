"""反思修复 Agent — 根据错误日志和模块代码，提出针对性修复方案。

RepairAgent 角色：
- 输入：blocked 模块的完整输出（规格、代码、测试、错误堆栈、审查意见）
- 输出：修复后的代码/测试，或明确放弃修复的原因
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from config import LLM_MAX_TOKENS, LLM_TIMEOUT_SECONDS
from utils import create_llm_json, extract_json


# ── 数据结构 ──────────────────────────────────────────────

@dataclass
class RepairResult:
    """修复结果。"""
    can_fix: bool
    reason: str = ""                # 无法修复时的原因
    code: str = ""                  # 修复后的代码
    test_code: str = ""             # 修复后的测试
    fix_summary: str = ""           # 修复方法摘要（用于案例库）
    raw_response: str = ""          # LLM 原始回复


# ── 系统提示词 ────────────────────────────────────────────

REPAIR_SYSTEM_PROMPT = """你是一位资深的自动修复工程师。你的任务是根据模块的完整输出（规格、代码、测试、错误堆栈、审查意见），提出针对性的修复方案。

## 你的能力边界

你可以处理：
1. **语法错误**：修复错误的语法、缺失的引号/括号、缩进问题等
2. **逻辑错误**：不正确的条件判断、不完整的函数实现、错误的参数传递
3. **测试失败**：测试断言不匹配、测试覆盖不足、测试数据不当
4. **审查问题**：缺少类型注解、缺少 docstring、异常处理不完整、安全问题
5. **依赖缺失**：分析缺失模块并生成最小实现骨架

你不能处理（应返回 can_fix: false）：
1. 需要访问外部服务/API 的问题
2. 需要数据库/网络操作的问题
3. 需求本身不明确或矛盾的问题
4. 代码完全空白无法分析的情况

## 输出格式（严格 JSON）

```json
{
  "can_fix": true,
  "reason": "",
  "code": "修复后的完整代码（包含类型注解、docstring、异常处理）",
  "test_code": "修复后的完整 pytest 测试代码",
  "fix_summary": "一句话描述修复了什么（例如：修改了函数签名，增加了输入校验，完善了异常处理）"
}
```

如果不确定能否修复：
```json
{
  "can_fix": false,
  "reason": "无法修复的原因（例如：需要外部 API 访问，无法进行）",
  "code": "",
  "test_code": "",
  "fix_summary": ""
}
```

## 修复规则
1. 修复后的代码必须包含完整的 import 语句。
2. 所有公共函数/类必须包含 type hints 和 docstring。
3. 异常处理必须覆盖外部调用。
4. 不得包含硬编码的密钥或密码。
5. 测试代码使用 pytest 风格，覆盖至少 2 个正常路径和 1 个异常路径。
6. 如果审查意见提到具体问题，务必逐条修复。
7. 输出必须是有效 JSON，代码内部的双引号用反斜杠转义。
8. 修复尽量保持最小化——只改需要改的部分，不要重写整个模块。
"""


# ── RepairAgent ──────────────────────────────────────────

class RepairAgent:
    """反思修复 Agent — 根据完整模块输出和错误信息生成修复方案。"""

    def __init__(self) -> None:
        self._llm = create_llm_json(
            max_tokens=LLM_MAX_TOKENS * 2,  # 修复代码可能较长
            timeout=LLM_TIMEOUT_SECONDS * 2,
        )

    async def repair(
        self,
        module_output: dict[str, Any],
        history_cases: list[dict[str, Any]] | None = None,
    ) -> RepairResult:
        """执行反思修复。

        Args:
            module_output: 模块完整输出，包含：
                - module_name: 模块名
                - module_type: 模块类型
                - spec: 模块规格
                - code: 当前代码
                - test_code: 当前测试代码
                - error_text: 错误/失败信息
                - review_issues: 审查指出的问题列表
            history_cases: 相似历史修复案例（可选），用于参考

        Returns:
            RepairResult
        """
        history_cases = history_cases or []

        # 构建 Prompt
        parts: list[str] = [REPAIR_SYSTEM_PROMPT]

        # 注入历史案例参考
        if history_cases:
            parts.append("\n## 📚 相似历史修复案例（仅供参考）\n")
            for i, case in enumerate(history_cases, 1):
                parts.append(
                    f"### 案例 {i}\n"
                    f"- 错误特征：{', '.join(case.get('error_keywords', [])[:10])}\n"
                    f"- 模块类型：{case.get('module_type', 'unknown')}\n"
                    f"- 修复方法：{case.get('fix_summary', '')}\n"
                    f"- 使用策略：{case.get('strategy_used', '')}\n"
                )

        # 模块信息
        module_name = module_output.get("module_name", "unknown")
        module_type = module_output.get("module_type", "backend")
        spec = module_output.get("spec", {})
        code = module_output.get("code", "")
        test_code = module_output.get("test_code", "")
        error_text = module_output.get("error_text", "")
        review_issues = module_output.get("review_issues", [])

        parts.append(f"""
## 🔧 待修复模块

- **模块名称**: {module_name}
- **模块类型**: {module_type}
- **功能规格**: {spec.get('summary', '未知')}
- **API 端点**: {', '.join(spec.get('api_endpoints', []))}
- **数据模型**: {', '.join(spec.get('data_models', []))}
- **核心流程**: {spec.get('logic_flow', '')}
- **异常场景**: {spec.get('error_handling', '')}
""")

        if error_text:
            parts.append(f"""
## ❌ 错误信息

```
{error_text[:3000]}
```
""")

        if review_issues:
            parts.append(f"""
## 🔍 审查问题

{chr(10).join(f'- {issue}' for issue in review_issues[:20])}
""")

        if code:
            parts.append(f"""
## 📝 当前代码

```python
{code[:4000]}
```
""")

        if test_code:
            parts.append(f"""
## 🧪 当前测试代码

```python
{test_code[:2000]}
```
""")

        parts.append("""
## 📋 任务

请分析以上信息，判断是否能够修复，并输出 JSON 格式的修复结果。

如果能够修复：
- code 字段包含修复后的完整代码
- test_code 字段包含修复后的测试代码
- fix_summary 字段用一句话描述修复方法

如果不能修复：
- can_fix 设为 false
- reason 字段说明为什么无法修复
""")

        prompt = "\n".join(parts)

        # 调用 LLM
        response = await self._llm.ainvoke(prompt)
        raw_text: str = (
            response.content if hasattr(response, "content") else str(response)
        )

        try:
            data = extract_json(raw_text)
        except ValueError as exc:
            return RepairResult(
                can_fix=False,
                reason=f"无法解析 RepairAgent 输出：{exc}",
                raw_response=raw_text,
            )

        return RepairResult(
            can_fix=data.get("can_fix", False),
            reason=data.get("reason", ""),
            code=data.get("code", ""),
            test_code=data.get("test_code", ""),
            fix_summary=data.get("fix_summary", ""),
            raw_response=raw_text,
        )
