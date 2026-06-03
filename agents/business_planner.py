"""BusinessPlannerAgent — 商业智能分析师。

基于市场调研、行业分析等商业文档生成商业项目计划建议书。
仅在文档类型被识别为 business 时激活。
"""

from __future__ import annotations

from typing import Any

from utils import create_llm_reasoning, extract_json

BUSINESS_PLANNER_PROMPT = """你是一位资深商业分析师与战略顾问。你的任务是基于提供的市场调研文档生成一份结构化的商业项目计划建议书。

## 核心约束

1. **文档优先**：你只能基于提供的文档摘要生成商业计划。不得凭空捏造市场数据、竞品信息或用户画像。
2. **信息不足即声明**：如果文档信息不足以支撑某个部分，在对应字段注明"根据现有资料无法确定，建议补充XX调研"。
3. **具体可操作**：all recommendations 必须具体到可以指导下一步行动的程度。

## 输出格式（严格 JSON）

```json
{
  "executive_summary": "300字以内的商业构想概述",
  "market_analysis": {
    "target_audience": "目标用户画像（基于文档数据）",
    "competition": "竞争格局与差异化机会",
    "trends": "相关行业趋势"
  },
  "product_positioning": "产品/服务定位与价值主张",
  "business_model": {
    "revenue_streams": ["收入来源1"],
    "cost_structure": "主要成本构成",
    "channels": ["销售/分发渠道"]
  },
  "roadmap": [
    {
      "phase": "阶段1名称",
      "duration": "预计时长",
      "actions": ["关键行动1"],
      "milestones": ["里程碑"]
    }
  ],
  "risks_and_mitigations": [
    {
      "risk": "风险描述",
      "severity": "high|medium|low",
      "mitigation": "对策"
    }
  ],
  "recommendations": "后续建议，包括是否需要技术开发及大概方向"
}
```

## 规则

1. executive_summary 控制在300字内，覆盖：机会、目标市场、核心价值、商业模式概要。
2. market_analysis 中的每个字段必须有文档数据支撑。
3. roadmap 至少包含2个阶段，每个阶段有明确的 actions 和 milestones。
4. 如果文档中明确提到数字（如"市场规模5万亿"），必须在计划中引用。
5. 不要添加与文档无关的行业常识——比如文档讲银发经济但你没看到健康管理数据，就不要编造健康管理相关建议。
"""


class BusinessPlannerAgent:
    """商业计划分析师。基于商业文档生成项目计划建议书。"""

    def __init__(self) -> None:
        self._llm = create_llm_reasoning()

    def _build_prompt(
        self,
        structured_context: dict[str, Any],
        user_requirement: str = "",
    ) -> str:
        parts: list[str] = [BUSINESS_PLANNER_PROMPT]

        overall = structured_context.get("overall_summary", "")
        if overall:
            parts.append(f"## 📋 项目全局描述\n{overall}")

        analyzed = structured_context.get("analyzed_files", [])
        if analyzed:
            parts.append(f"\n## 📄 目录资料摘要（共 {len(analyzed)} 个文件）")
            for af in analyzed:
                parts.append(f"\n### {af['file']}\n{af['summary']}")

        if user_requirement.strip():
            parts.append(
                f"\n## 用户文字指令（与以上目录资料一并纳入商业计划）\n{user_requirement.strip()}"
            )
        else:
            parts.append(
                "\n## 用户文字指令\n（未单独填写，请主要依据目录资料；若有代码文件列表亦作背景参考）"
            )

        parts.append("\n请综合文字指令与目录资料，生成商业计划 JSON。")
        return "\n\n".join(parts)

    async def plan(
        self,
        structured_context: dict[str, Any],
        project_memory_text: str = "",
        user_requirement: str = "",
    ) -> dict[str, Any]:
        """生成商业计划建议书（主入口方法）。

        Args:
            structured_context: 包含 analyzed_files + overall_summary 的上下文
            project_memory_text: 项目记忆

        Returns:
            商业计划 JSON，格式见 BUSINESS_PLANNER_PROMPT
        """
        return await self.generate(structured_context, project_memory_text, user_requirement)

    async def generate(
        self,
        structured_context: dict[str, Any],
        project_memory_text: str = "",
        user_requirement: str = "",
    ) -> dict[str, Any]:
        """生成商业计划建议书（底层实现，与 plan() 等效）。

        Args:
            structured_context: 包含 analyzed_files + overall_summary 的上下文
            project_memory_text: 项目记忆

        Returns:
            商业计划 JSON
        """
        prompt = self._build_prompt(structured_context, user_requirement)
        if project_memory_text:
            prompt += f"\n\n{project_memory_text}"

        response = await self._llm.ainvoke(prompt)
        raw_text: str = response.content if hasattr(response, "content") else str(response)
        result = extract_json(raw_text)
        return result

    @staticmethod
    def validate(result: dict[str, Any]) -> list[str]:
        """验证商业计划结果的结构完整性。

        Returns:
            错误信息列表。空列表表示通过。
        """
        import logging

        _logger = logging.getLogger(__name__)
        errors: list[str] = []

        # ── 顶层必填字段 ──
        required_str_fields = [
            ("executive_summary", "执行摘要"),
            ("product_positioning", "产品定位"),
            ("recommendations", "建议"),
        ]
        for key, label in required_str_fields:
            val = result.get(key, "")
            if not val or not isinstance(val, str) or not val.strip():
                errors.append(f"缺少或无效的 '{key}'（{label}）")

        # ── market_analysis ──
        ma = result.get("market_analysis")
        if not isinstance(ma, dict):
            errors.append("'market_analysis' 必须是对象")
        else:
            for sub in ["target_audience", "competition", "trends"]:
                sv = ma.get(sub, "")
                if not sv or not isinstance(sv, str) or not sv.strip():
                    errors.append(f"market_analysis 缺少或无效的 '{sub}'")

        # ── business_model ──
        bm = result.get("business_model")
        if not isinstance(bm, dict):
            errors.append("'business_model' 必须是对象")
        else:
            if not isinstance(bm.get("revenue_streams"), list):
                errors.append("business_model.revenue_streams 必须是数组")
            for sub in ["cost_structure"]:
                sv = bm.get(sub, "")
                if not sv or not isinstance(sv, str) or not sv.strip():
                    errors.append(f"business_model 缺少或无效的 '{sub}'")

        # ── roadmap ──
        roadmap = result.get("roadmap")
        if not isinstance(roadmap, list):
            errors.append("'roadmap' 必须是数组")
        elif len(roadmap) < 2:
            errors.append(
                f"roadmap 至少需要 2 个阶段，当前只有 {len(roadmap)} 个"
            )
        else:
            for i, phase in enumerate(roadmap):
                if not isinstance(phase, dict):
                    errors.append(f"roadmap #{i}: 必须是对象")
                    continue
                for f in ["phase", "duration"]:
                    if not phase.get(f):
                        errors.append(f"roadmap #{i}: 缺少 '{f}'")
                for f in ["actions", "milestones"]:
                    if not isinstance(phase.get(f), list) or len(phase[f]) == 0:
                        errors.append(f"roadmap #{i}: '{f}' 必须是非空数组")

        # ── risks_and_mitigations ──
        risks = result.get("risks_and_mitigations")
        if not isinstance(risks, list):
            errors.append("'risks_and_mitigations' 必须是数组")
        else:
            for i, r in enumerate(risks):
                if not isinstance(r, dict):
                    errors.append(f"risks_and_mitigations #{i}: 必须是对象")
                    continue
                if not r.get("risk"):
                    errors.append(f"risks_and_mitigations #{i}: 缺少 'risk'")
                sev = r.get("severity", "")
                if sev not in ("high", "medium", "low"):
                    errors.append(
                        f"risks_and_mitigations #{i}: "
                        f"severity 必须是 high/medium/low，当前为 '{sev}'"
                    )

        if errors:
            _logger.warning("商业计划验证失败: %s", "; ".join(errors))

        return errors

    @staticmethod
    def format_for_display(result: dict[str, Any]) -> dict[str, Any]:
        """格式化为前端展示结构。"""
        return {
            "status": "ok",
            "plan_type": "business",
            "executive_summary": result.get("executive_summary", ""),
            "market_analysis": result.get("market_analysis", {}),
            "product_positioning": result.get("product_positioning", ""),
            "business_model": result.get("business_model", {}),
            "roadmap": result.get("roadmap", []),
            "risks_and_mitigations": result.get("risks_and_mitigations", []),
            "recommendations": result.get("recommendations", ""),
        }
