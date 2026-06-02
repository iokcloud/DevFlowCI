"""需求输入统一：用户文字 + 目录扫描资料视为同一需求的不同来源。"""

from __future__ import annotations

import os
from datetime import UTC
from typing import Any


def persist_requirement_text(user_text: str, directory: str | None) -> str:
    """写入数据库/历史列表的需求摘要（非空）。用户文字优先，否则用目录标识。"""
    text = (user_text or "").strip()
    if text:
        return text
    if directory:
        name = os.path.basename(directory.rstrip("/\\")) or directory
        return f"基于目录「{name}」的资料与代码上下文进行分析"
    return ""


def user_instruction(user_text: str) -> str:
    """用户主动输入的文字指令（可为空）。"""
    return (user_text or "").strip()


def has_directory_context(structured_context: dict[str, Any] | None) -> bool:
    """目录扫描是否提供了可对齐分析的有效上下文。"""
    if not structured_context:
        return False
    if structured_context.get("analyzed_files"):
        return True
    if structured_context.get("source_files"):
        return True
    summary = (structured_context.get("overall_summary") or "").strip()
    if summary and not structured_context.get("no_documentation_found", True):
        return True
    return False


def merge_sources_label(user_text: str, directory: str | None) -> str:
    """日志/进度用：说明本次分析合并了哪些来源。"""
    parts: list[str] = []
    if user_instruction(user_text):
        parts.append("文字指令")
    if (directory or "").strip():
        parts.append("目录资料")
    if not parts:
        return "需求"
    return " + ".join(parts)


def parse_requirement_addenda(raw_json: str | None) -> list[dict[str, str]]:
    """解析需求补充记录列表。"""
    if not raw_json:
        return []
    import json

    try:
        data = json.loads(raw_json)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(data, list):
        return []
    out: list[dict[str, str]] = []
    for item in data:
        if isinstance(item, dict) and (item.get("text") or "").strip():
            out.append(
                {
                    "round": str(item.get("round", "")),
                    "text": str(item["text"]).strip(),
                    "created_at": str(item.get("created_at", "")),
                }
            )
    return out


def append_requirement_addendum(
    raw_json: str | None,
    *,
    text: str,
    round_num: int,
) -> str:
    """追加一条需求补充并返回 JSON 字符串。"""
    import json
    from datetime import datetime

    entries = parse_requirement_addenda(raw_json)
    entries.append(
        {
            "round": round_num,
            "text": text.strip(),
            "created_at": datetime.now(UTC).isoformat(),
        }
    )
    return json.dumps(entries, ensure_ascii=False)


def build_effective_requirement(
    base_requirement: str,
    addenda: list[dict[str, str]],
) -> str:
    """合并原始需求与各轮补充，供 Agent 使用。"""
    parts = [(base_requirement or "").strip()]
    for i, item in enumerate(addenda, 1):
        label = item.get("round") or i
        parts.append(f"【第 {label} 轮补充】\n{item['text']}")
    merged = "\n\n".join(p for p in parts if p)
    return merged or (base_requirement or "")


def build_iteration_context(
    *,
    iteration: int,
    addendum_text: str = "",
    global_review: dict | None = None,
) -> str:
    """迭代轮次附加上下文，注入 project_context。"""
    lines = [f"当前为第 {iteration} 轮迭代改进。"]
    if addendum_text.strip():
        lines.append(f"本轮用户补充：{addendum_text.strip()}")
    review = global_review or {}
    if review:
        score = review.get("score")
        if score is not None:
            lines.append(f"上一轮审查评分：{score}/100")
        issues = review.get("blocked_module_issues") or review.get("issues") or []
        if issues:
            lines.append("待解决问题：" + "；".join(str(x) for x in issues[:6]))
        suggestions = review.get("suggestions") or []
        if suggestions:
            lines.append("改进建议：" + "；".join(str(x) for x in suggestions[:4]))
    return "\n".join(lines)
