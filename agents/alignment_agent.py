"""AlignmentAgent — 需求对齐分析师。

在 PM 规划之前运行，负责：
1. 融合用户需求和项目文档摘要，产出基于实际内容的执行计划
2. 识别技术栈假设、环境假设和潜在风险
3. 生成结构化对齐 JSON，供用户确认后进入规划阶段
4. 自检模块与文档的关联性，标记存疑项
"""

from __future__ import annotations

import json
import re
from typing import Any

from utils import create_llm, extract_json

# ── 系统提示词（重写：硬约束版本）────────────────────────

ALIGNMENT_SYSTEM_PROMPT = """你是一位严格的需求分析师。你的任务是基于**项目文档实际内容**生成执行计划建议。

## 核心约束（违反将导致输出无效）

1. **文档优先**：你只能基于下面提供的「项目文档摘要」和「用户需求」生成计划。不得凭空添加任何文档中未提到的技术栈、模块或修复点。
2. **禁止通用建议**：不得输出"改善代码质量"、"增强日志系统"、"添加单元测试"等通用建议，除非文档中明确描述了相关痛点。
3. **信息不足即声明**：如果文档信息不足以生成有意义的计划，直接输出 `{"status": "insufficient_info", "message": "具体原因"}`，不要强行编造。
4. **关联溯源**：plan 中每个模块的 reason 字段必须引用至少一个文档文件名或具体段落，说明该建议的来源。

## 输出格式（严格 JSON，不要包含其他文字）

### 正常情况（有足够信息）

```json
{
  "summary": "基于文档分析的需求摘要（1-3句话）",
  "assumptions": ["仅在文档支持时的技术假设"],
  "risks": ["仅在文档中提到的风险"],
  "plan": [
    {
      "module": "snake_case名称",
      "description": "基于文档的具体修改内容",
      "reason": "为什么需要：引用文档中的具体描述",
      "type": "backend|frontend|database|integration|testing",
      "evidence": {
        "file": "引用的文档文件名（如 README.md）",
        "excerpt": "文档中相关片段（1-2句）"
      }
    }
  ],
  "questions": ["需用户澄清的问题"]
}
```

### 信息不足情况

```json
{
  "status": "insufficient_info",
  "message": "当前目录下的文档不足以自动生成执行计划，建议输入具体需求或补充项目说明文档。具体原因：……"
}
```

## 规则

1. 每个 module.description 必须能在提供的文档摘要中找到直接依据。
2. reason 字段必须标注来源（文档文件名或段落）。
3. 如果用户需求为空且目录文档也不足 → 必须返回 insufficient_info。
4. 如果文档中提到了技术栈（如 FastAPI、React），在 assumptions 中引用；否则不要假设。
5. plan 可以为空数组（如果确实没有需要修改的地方），但这与 insufficient_info 不同——前者是"分析完毕，无需修改"，后者是"无法分析"。
"""

# ── 构建 Prompt ──────────────────────────────────────────


def _build_structured_prompt(
    requirement: str,
    project_context: str = "",
    project_memory_text: str = "",
) -> str:
    """构建包含结构化文档摘要的 Prompt。"""
    parts: list[str] = [ALIGNMENT_SYSTEM_PROMPT]

    # 解析项目上下文 JSON
    context_json: dict[str, Any] = {}
    if project_context:
        try:
            context_json = json.loads(project_context)
        except (json.JSONDecodeError, TypeError):
            parts.append("## 📂 项目上下文（旧格式）")
            parts.append(project_context[:3000])

    analyzed_files = context_json.get("analyzed_files", [])
    overall_summary = context_json.get("overall_summary", "")
    no_docs = context_json.get("no_documentation_found", False)
    source_files = context_json.get("source_files", [])
    project_type = context_json.get("project_type", "未知")

    if overall_summary:
        parts.append(f"## 📋 项目全局描述\n{overall_summary}")
        parts.append(f"项目类型: {project_type}")

    if analyzed_files:
        parts.append(f"\n## 📄 文档摘要（共 {len(analyzed_files)} 个文件，计划必须基于这些内容）")
        for af in analyzed_files:
            parts.append(f"\n### {af['file']}\n{af['summary']}")

    if source_files:
        parts.append(f"\n## 📦 源代码文件列表\n{', '.join(source_files[:20])}")

    if no_docs and not requirement.strip():
        parts.append("\n## ⚠️ 重要提示")
        parts.append("目录下无文档文件，用户也未输入需求。请直接返回 insufficient_info。")

    if project_memory_text:
        parts.append(project_memory_text)

    if requirement.strip():
        parts.append(f"\n## 用户需求\n{requirement}")
    else:
        parts.append("\n## 用户需求\n（用户未输入具体需求，请仅基于文档摘要生成计划）")

    parts.append("\n请严格遵循核心约束，输出上述 JSON 格式的结果。")

    return "\n\n".join(parts)


# ── 自检逻辑 ──────────────────────────────────────────────


def _self_check_plan(
    plan: list[dict[str, Any]],
    analyzed_files: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """检查 plan 中每个模块是否能在文档摘要中找到依据。

    对于找不到关联的模块，标记 flagged: true 和 warning 信息。
    """
    if not analyzed_files:
        for m in plan:
            m["flagged"] = True
            m["warning"] = "无文档可供交叉验证，请人工确认此模块是否必要"
        return plan

    all_doc_text = " ".join(af.get("summary", "") for af in analyzed_files).lower()

    for m in plan:
        desc = (m.get("description", "") + " " + m.get("reason", "")).lower()
        module_name = m.get("module", "").lower().replace("_", " ")

        words = set(re.findall(r"[a-zA-Z\u4e00-\u9fff]{2,}", desc))
        matched = any(w.lower() in all_doc_text for w in words if len(w) >= 3)

        evidence = m.get("evidence", {})
        has_evidence = bool(evidence.get("file", ""))

        file_match = any(
            module_name in af.get("file", "").lower()
            or af.get("file", "").lower().replace(".md", "").replace("_", " ") in desc
            for af in analyzed_files
        )

        if not matched and not has_evidence and not file_match:
            m["flagged"] = True
            m["warning"] = "此模块未找到文档依据，请确认是否需要"

    return plan


# ── AlignmentAgent 类 ─────────────────────────────────────


class AlignmentAgent:
    """需求对齐分析师 Agent。"""

    def __init__(self) -> None:
        self._llm = create_llm()

    @staticmethod
    def validate_alignment(result: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        if result.get("status") == "insufficient_info":
            if not result.get("message"):
                errors.append("insufficient_info 缺少 message")
            return errors
        for field in ["summary", "assumptions", "risks", "plan", "questions"]:
            if field not in result:
                errors.append(f"缺少 '{field}'")
        if "plan" in result:
            plan = result["plan"]
            if not isinstance(plan, list):
                errors.append("'plan' 必须是数组")
            else:
                for i, m in enumerate(plan):
                    for f in ["module", "description", "reason"]:
                        if not m.get(f):
                            errors.append(f"plan #{i}: 缺少 '{f}'")
        return errors

    async def analyze(
        self,
        requirement: str,
        project_context: str = "",
        project_memory_text: str = "",
    ) -> tuple[dict[str, Any], list[str]]:
        prompt = _build_structured_prompt(requirement, project_context, project_memory_text)
        response = await self._llm.ainvoke(prompt)
        raw_text: str = response.content if hasattr(response, "content") else str(response)
        result = extract_json(raw_text)

        # ── 自检 ──
        plan = result.get("plan", [])
        if plan:
            analyzed_files: list[dict[str, Any]] = []
            if project_context:
                try:
                    ctx = json.loads(project_context)
                    analyzed_files = ctx.get("analyzed_files", [])
                except (json.JSONDecodeError, TypeError):
                    pass
            result["plan"] = _self_check_plan(plan, analyzed_files)

        errors = self.validate_alignment(result)
        return result, errors

    async def analyze_alternatives(
        self,
        requirement: str,
        project_context: str = "",
        project_memory_text: str = "",
    ) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
        plan_a, errors = await self.analyze(requirement, project_context, project_memory_text)
        return plan_a, {}, errors

    @staticmethod
    def format_alignment_for_display(result: dict[str, Any]) -> dict[str, Any]:
        return {
            "status": result.get("status", "ok"),
            "summary": result.get("summary", ""),
            "message": result.get("message", ""),
            "assumptions": result.get("assumptions", []),
            "risks": result.get("risks", []),
            "plan": result.get("plan", []),
            "questions": result.get("questions", []),
            "label": result.get("label", ""),
            "approach": result.get("approach", ""),
        }
