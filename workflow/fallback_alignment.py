"""兜底对齐生成 — 当 LLM 对齐失败时的降级策略。

从 workflow.executor 中独立出来。
"""

from __future__ import annotations

from typing import Any

# ── 兜底对齐生成 ──────────────────────────────────────────

# ── 兜底对齐生成 ──────────────────────────────────────────

def _fallback_alignment(
    requirement: str,
    structured_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """当 LLM 调用失败或返回 insufficient_info 时，生成智能兜底对齐方案。

    确保流程不会因为 LLM 故障而完全阻塞。
    如果有项目文档上下文，会生成更准确的兜底方案。
    """
    import re

    analyzed_files: list[dict[str, Any]] = []
    if structured_context:
        analyzed_files = structured_context.get("analyzed_files", [])
        # 尝试从 overall_summary 提取有用信息

    # ── 场景1：有文档但无法分析 → 告知用户并提供建议 ──
    if analyzed_files:
        file_names = [af.get("file", "未知") for af in analyzed_files[:5]]
        file_list = "、".join(file_names)
        doc_type = structured_context.get("document_type", "generic") if structured_context else "generic"
        summary = (
            f"目录中有 {len(analyzed_files)} 个文档文件（{file_list}等），"
            f"但 AI 自动分析未能生成完整计划。"
        )
        modules = [{
            "module": "文档分析建议",
            "description": f"检测到文档类型为「{doc_type}」，建议在需求框中输入具体的开发/分析需求",
            "reason": "目录中文档不足以自动推导代码开发计划，需要用户提供明确指令",
            "type": "backend",
        }]
        return {
            "summary": summary,
            "assumptions": [
                f"文档类型: {doc_type}",
                "假设：用户将在需求框中补充具体需求后重新提交",
            ],
            "risks": [
                "当前自动分析未生成可执行模块",
                "建议输入具体开发需求（如「基于这些市场数据开发一个分析工具」）",
            ],
            "plan": modules,
            "questions": [
                "您希望基于这些文档做什么？（如：生成市场分析报告、开发数据分析工具、制作演示文稿等）",
            ],
        }

    # ── 场景2：无文档，纯文本需求 → 以完整需求为单模块 MVP ──
    req = (requirement or "").strip()
    if req:
        slug = re.sub(r"[^\w\u4e00-\u9fff]+", "_", req[:24]).strip("_").lower() or "core_mvp"
        modules = [{
            "module": slug[:30],
            "description": req[:800],
            "reason": "用户明确提出的功能需求（纯文本模式）",
            "type": "backend",
        }]
        return {
            "summary": f"基于用户需求生成 MVP 计划：{req[:100]}",
            "assumptions": ["默认 Python 实现", "优先最小可运行交付"],
            "risks": ["若需求范围过大，将在模块构建阶段按 MVP 上限裁剪"],
            "plan": modules,
            "questions": [],
        }

    words = re.findall(r"[\u4e00-\u9fff]+|[a-zA-Z]+", requirement)
    keywords = [w for w in words if len(w) >= 2][:5]

    modules = []
    if keywords:
        for _i, kw in enumerate(keywords[:4]):
            modules.append({
                "module": kw.lower().replace(" ", "_")[:30],
                "description": f"实现与「{kw}」相关的功能",
                "reason": f"需求中提到了「{kw}」，需要实现相关功能",
                "type": "backend",
            })
    else:
        modules.append({
            "module": "main_module",
            "description": requirement[:80],
            "reason": "实现用户需求的核心功能",
            "type": "backend",
        })

    return {
        "summary": f"基于用户需求「{requirement[:80]}...」的基础执行计划（AI 分析暂不可用，使用模板方案）",
        "assumptions": [
            "技术栈假设：Python 3.12+（默认）",
            "环境假设：本地开发环境",
            "注：此方案由模板生成，建议人工审查",
        ],
        "risks": [
            "AI 分析未完成，模块划分可能不够精确",
            "建议人工审查并调整计划",
        ],
        "plan": modules,
        "questions": ["请人工审查此自动生成的计划是否满足需求"],
    }


