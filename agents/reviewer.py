"""审查者 Agent — 对模块产出做 PASS/FAIL 判定。

审查员返回必须有 PASS 或 FAIL: ... 的明确标记，
程序使用正则提取。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from utils import create_llm


# ── 数据结构 ──────────────────────────────────────────────

@dataclass
class ReviewResult:
    """审查结果。"""
    passed: bool
    summary: str
    issues: list[str] = field(default_factory=list)
    raw_response: str = ""


# ── 系统提示词 ────────────────────────────────────────────

REVIEWER_SYSTEM_PROMPT = """你是一位严格的代码审查员。你的任务是对一个软件模块进行全面审查。

## 必须审查的方面
1. **功能**：代码是否实现了规格要求？
2. **质量**：类型注解、docstring、代码风格是否符合规范？
3. **安全**：是否存在 SQL 注入、硬编码密钥、路径遍历、未校验输入？
4. **异常处理**：外部调用是否有 try-except？异常是否正确传播？
5. **测试**：测试是否覆盖了核心路径和异常路径？

## 输出格式（非常重要！）
你的审查结论 **必须以 PASS 或 FAIL 开头**，格式严格如下：

如果通过：
```
PASS
所有审查项通过。代码质量良好。
```

如果不通过：
```
FAIL: 模块名称
1. 缺少用户名输入校验（安全）
2. 数据库异常未处理（异常处理）
3. 缺少 docstring（质量）
```

**规则**：
1. 第一行必须是 PASS 或 FAIL（后跟可选的 : 和模块名）。
2. FAIL 时，列出所有具体问题（每个一行，序号开头）。
3. 只输出审查结论，不要输出其他无关内容。
"""


REVIEWER_MVP_ADDON = """

## MVP 模式（单模块最小交付）
当前为 MVP 单模块，审查标准适当放宽：
- 核心功能与基本测试覆盖到位即可 PASS
- 轻微风格/docstring 瑕疵可写入 summary，但不要因此 FAIL
- 勿因缺少非关键边缘 case 或过度完美的异常处理而 FAIL
"""


# ── 审查者 Agent ──────────────────────────────────────────

class ReviewerAgent:
    """模块级代码审查员。"""

    # 提取 PASS/FAIL 的正则
    REVIEW_REGEX = re.compile(
        r"^(PASS|FAIL)(?::\s*(.+))?$", re.MULTILINE | re.IGNORECASE
    )

    def __init__(self) -> None:
        self._llm = create_llm(temperature=0.1)  # 审查用更低温度

    @staticmethod
    def extract_verdict(text: str) -> ReviewResult:
        """从审查回复中提取 PASS/FAIL 判定。

        Args:
            text: 审查员的原始回复

        Returns:
            ReviewResult
        """
        match = ReviewerAgent.REVIEW_REGEX.search(text)
        if not match:
            # Fallback: 尝试在文本中找 PASS 或 FAIL 关键词
            if "PASS" in text.upper() and "FAIL" not in text.upper():
                return ReviewResult(
                    passed=True,
                    summary=text.strip()[:500],
                    raw_response=text,
                )
            elif "FAIL" in text.upper():
                return ReviewResult(
                    passed=False,
                    summary=text.strip()[:500],
                    issues=[text.strip()],
                    raw_response=text,
                )
            # 无法判断，默认失败
            return ReviewResult(
                passed=False,
                summary="无法从审查输出中提取有效判定",
                issues=["审查输出格式不符合规范：缺少 PASS/FAIL 标记"],
                raw_response=text,
            )

        verdict = match.group(1).upper()
        passed = verdict == "PASS"

        # 提取 issues（FAIL 时）
        issues: list[str] = []
        if not passed:
            lines = text.strip().split("\n")[1:]  # 跳过第一行
            for line in lines:
                line = line.strip()
                if line and (line[0].isdigit() or line.startswith("- ")):
                    # 清理序号
                    clean = re.sub(r"^\d+[\.\)]\s*", "", line)
                    clean = re.sub(r"^-\s*", "", clean)
                    if clean:
                        issues.append(clean)
            if not issues:
                issues.append(text.strip())

        # summary 取第一行之后的内容
        summary_lines = text.strip().split("\n")[1:]
        summary = "\n".join(summary_lines).strip()[:500]

        return ReviewResult(
            passed=passed,
            summary=summary or text.strip()[:500],
            issues=issues,
            raw_response=text,
        )

    async def review(
        self,
        module_name: str,
        spec_summary: str,
        code: str,
        test_code: str,
        retry_count: int = 0,
        mvp_mode: bool = False,
    ) -> ReviewResult:
        """执行代码审查。

        Args:
            module_name: 模块名称
            spec_summary: 模块规格摘要
            code: 模块代码
            test_code: 测试代码
            retry_count: 当前重试次数（用于上下文）
            mvp_mode: MVP 单模块时放宽审查标准

        Returns:
            ReviewResult
        """
        retry_note = ""
        if retry_count > 0:
            retry_note = (
                f"\n⚠️ 这是第 {retry_count} 次审查重试。"
                f"请特别注意之前指出的问题是否已修复。\n"
            )

        mvp_note = REVIEWER_MVP_ADDON if mvp_mode else ""

        prompt = f"""{REVIEWER_SYSTEM_PROMPT}{mvp_note}

{retry_note}
模块名称：{module_name}
功能规格摘要：{spec_summary}

代码：
```python
{code[:4000]}
```

测试代码：
```python
{test_code[:2000]}
```

请输出审查结论（PASS 或 FAIL 开头）。"""

        response = await self._llm.ainvoke(prompt)
        raw_text: str = (
            response.content if hasattr(response, "content") else str(response)
        )
        return self.extract_verdict(raw_text)
