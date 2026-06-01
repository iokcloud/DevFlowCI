"""集成者 Agent + 全局审查者 Agent。

集成者：将各模块代码组装为完整项目结构，生成集成测试。
全局审查者：检查跨模块一致性、接口对接、最终质量。
支持 blocked 模块：跳过其功能测试，生成提醒。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from config import LLM_MAX_TOKENS, LLM_TIMEOUT_SECONDS
from utils import create_llm_json, extract_json


# ── 数据结构 ──────────────────────────────────────────────

@dataclass
class IntegrationResult:
    """集成结果。"""
    project_structure: str         # 建议的文件结构
    main_code: str                 # 主入口/组装代码
    integration_tests: str         # 集成测试
    readme: str                    # README 内容
    requirements: str              # 依赖清单


@dataclass
class GlobalReviewResult:
    """全局审查结果。"""
    passed: bool
    score: int                     # 0-100
    summary: str
    issues: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    blocked_module_issues: list[str] = field(default_factory=list)  # 因 blocked 模块导致的接口断裂


# ── 系统提示词 ────────────────────────────────────────────

INTEGRATOR_SYSTEM_PROMPT = """你是一位 DevOps 集成工程师。你的任务是将多个独立的模块代码组装成一个完整的、可运行的项目。

## 输出格式（严格 JSON）

```json
{
  "project_structure": "项目的文件树，每个文件标注用途",
  "main_code": "主入口文件代码（如 main.py / app.py）",
  "integration_tests": "跨模块集成测试代码（pytest 格式）",
  "readme": "# 项目名称\\n\\n## 安装与运行\\n```bash\\npip install -r requirements.txt\\npython main.py\\n```\\n\\n## API 文档\\n...",
  "requirements": "fastapi==0.115.6\\nuvicorn==0.34.0\\n..."
}
```

## 要求
1. main_code 必须能正确导入各已完成的模块，对于标记为 blocked 的模块使用 try-except 导入或跳过。
2. integration_tests 至少包含 2 个端到端测试场景，但不要测试 blocked 模块的功能。
3. README 包含安装、运行、API 说明；包含 **自治运维 (Autopilot)** 章节说明 autopilot/ 模块；如有 blocked 模块需在 README 中注明。
4. requirements 列出所有代码中实际使用的包。
5. 输出必须是有效 JSON，代码中的引号需转义。

## 关于 Blocked 模块
- 标记为 blocked 的模块只包含占位骨架代码，不包含业务逻辑。
- 集成时仍将这些占位文件放置在正确的项目结构中。
- 集成测试应跳过这些 blocked 模块的功能，但可以测试已完成的模块之间的交互。
"""

GLOBAL_REVIEWER_SYSTEM_PROMPT = """你是一位资深技术总监，负责对整个项目做全局质量审查。

## 审查维度
1. **模块一致性**：模块间接口是否正确对接？
2. **代码质量**：整体代码风格是否统一？
3. **异常处理**：全局异常处理是否完备？
4. **可运行性**：项目能否直接运行？
5. **文档完整性**：README 是否清晰？
6. **Blocked 模块影响**：未完成的模块是否会导致接口断裂？

## 输出格式（严格 JSON）

```json
{
  "passed": true,
  "score": 85,
  "issues": [],
  "suggestions": ["建议添加 Docker 支持", "建议添加日志系统"],
  "blocked_module_issues": ["模块 'payment' 的接口缺失，影响 'order' 模块的支付流程"],
  "summary": "项目整体质量良好，但注意有 2 个模块尚未完成。"
}
```

## 评分标准
- 90-100：优秀，无需修改
- 70-89：良好，有少量改进建议
- 50-69：一般，需要修复一些问题
- <50：不合格，必须重新处理

输出必须是有效 JSON。
"""


# ── Agent 类 ──────────────────────────────────────────────

class IntegratorAgent:
    """集成工程师 Agent。"""

    def __init__(self) -> None:
        self._llm = create_llm_json(
            max_tokens=LLM_MAX_TOKENS * 2,  # 集成代码较长
            timeout=LLM_TIMEOUT_SECONDS * 2,
        )

    async def integrate(
        self,
        project_requirement: str,
        modules: list[dict[str, Any]],
    ) -> IntegrationResult:
        """组装各模块为完整项目。

        Args:
            project_requirement: 用户原始需求
            modules: 各模块信息列表 [{"module_name": ..., "code": ..., "spec": ..., "status": ...}, ...]
        """
        # 区分正常模块和 blocked 模块
        normal_modules = []
        blocked_modules = []
        for m in modules:
            if m.get("status") == "blocked":
                blocked_modules.append(m)
            else:
                normal_modules.append(m)

        modules_text_parts: list[str] = []
        for m in normal_modules:
            modules_text_parts.append(
                f"## 模块：{m['module_name']} ✓\n"
                f"**功能**：{m.get('description', m.get('spec', ''))}\n"
                f"```python\n{m.get('code', '')[:2000]}\n```\n"
            )
        modules_text = "\n".join(modules_text_parts)

        blocked_note = ""
        if blocked_modules:
            blocked_names = ", ".join(m["module_name"] for m in blocked_modules)
            blocked_note = (
                f"\n\n⚠️ 以下模块尚未完成（blocked），仅含占位代码，集成测试请跳过其功能：\n"
                + "\n".join(f"- **{m['module_name']}**: {m.get('description', '')[:100]}" for m in blocked_modules)
                + f"\n集成时请将上述占位文件放置到项目结构的正确位置，但测试时请跳过它们。"
            )

        prompt = f"""{INTEGRATOR_SYSTEM_PROMPT}

用户需求：{project_requirement}

各模块代码：
{modules_text}
{blocked_note}

请根据这些模块，输出上述 JSON 格式的集成结果。"""

        response = await self._llm.ainvoke(prompt)
        raw_text = response.content if hasattr(response, "content") else str(response)
        data = extract_json(raw_text)

        return IntegrationResult(
            project_structure=data.get("project_structure", ""),
            main_code=data.get("main_code", ""),
            integration_tests=data.get("integration_tests", ""),
            readme=data.get("readme", ""),
            requirements=data.get("requirements", ""),
        )


class GlobalReviewerAgent:
    """全局审查者 Agent。"""

    def __init__(self) -> None:
        self._llm = create_llm_json(temperature=0.2)

    async def review(
        self,
        project_requirement: str,
        plan: list[dict[str, Any]],
        modules: list[dict[str, Any]],
        integration: IntegrationResult,
    ) -> GlobalReviewResult:
        """执行全局审查。

        Args:
            project_requirement: 用户原始需求
            plan: PM 规划的任务列表
            modules: 所有模块信息
            integration: 集成结果

        Returns:
            GlobalReviewResult
        """
        # 区分 blocked 模块
        blocked_names = [m["module_name"] for m in modules if m.get("status") == "blocked"]
        blocked_section = ""
        if blocked_names:
            blocked_section = (
                "\n\n⚠️ 以下模块未完成（blocked）：\n" +
                "\n".join(f"- **{name}**" for name in blocked_names) +
                "\n请特别审查这些缺失是否导致接口断裂。"
            )

        modules_summary = "\n".join(
            f"- **{m['module_name']}** [{m.get('status', '?')}]：{m.get('description', m.get('spec', ''))[:100]}"
            for m in modules
        )

        prompt = f"""{GLOBAL_REVIEWER_SYSTEM_PROMPT}

用户需求：{project_requirement}

模块列表：
{modules_summary}
{blocked_section}

集成结构：
{str(integration.project_structure)[:1000]}

主入口代码（部分）：
```python
{integration.main_code[:2000]}
```

请输出 JSON 格式的全局审查结果。"""

        response = await self._llm.ainvoke(prompt)
        raw_text = response.content if hasattr(response, "content") else str(response)
        data = extract_json(raw_text)

        return GlobalReviewResult(
            passed=data.get("passed", False),
            score=data.get("score", 0),
            summary=data.get("summary", ""),
            issues=data.get("issues", []),
            suggestions=data.get("suggestions", []),
            blocked_module_issues=data.get("blocked_module_issues", []),
        )
