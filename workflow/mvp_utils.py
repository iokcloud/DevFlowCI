"""商业 MVP 辅助函数 — 模块数量上限、MVP 测试门禁等。

从 workflow.executor 中独立出来。
"""

from __future__ import annotations

from typing import Any

from config import BUSINESS_MVP_MAX_MODULES
from workflow.langgraph_def import WorkflowState


def _resolve_mvp_module_cap(state: WorkflowState) -> int | None:
    """商业 MVP 或 state 显式指定的模块上限。"""
    cap = state.get("mvp_max_modules")
    if cap is not None:
        try:
            return max(1, int(cap))
        except (TypeError, ValueError):
            pass
    alignment = state.get("alignment_result") or {}
    if isinstance(alignment, dict):
        stored = alignment.get("mvp_max_modules")
        if stored is not None:
            try:
                return max(1, int(stored))
            except (TypeError, ValueError):
                pass
        if alignment.get("plan_type") == "business":
            return BUSINESS_MVP_MAX_MODULES
    return None


def _normalize_business_mvp_modules(
    modules: list[dict[str, Any]],
    state: WorkflowState,
) -> list[dict[str, Any]]:
    """商业 cap=1 时将 PM 产出合并为单模块，描述对齐已确认的技术需求。"""
    cap = _resolve_mvp_module_cap(state)
    if cap != 1 or len(modules) <= 1:
        return modules
    requirement = (state.get("requirement") or "").strip()
    primary = modules[0]
    name = primary.get("module_name") or primary.get("module") or "business_mvp"
    desc = requirement[:600] if requirement else (primary.get("description") or name)
    return [{
        "module_name": name,
        "description": desc,
        "dependencies": [],
        "type": primary.get("type", "backend"),
    }]


def _exec_tests_passed(exec_test_result: dict[str, Any] | None) -> bool:
    """单元测试是否全部通过（用于 MVP 放宽）。"""
    if not exec_test_result:
        return False
    mode = exec_test_result.get("execution_mode", "")
    if mode in ("skipped", "error"):
        return False
    passed = int(exec_test_result.get("passed") or 0)
    failed = int(exec_test_result.get("failed") or 0)
    errors = int(exec_test_result.get("errors") or 0)
    return passed > 0 and failed == 0 and errors == 0


def _exec_tests_skipped(exec_test_result: dict[str, Any] | None) -> bool:
    """未执行真实 pytest（无测试代码或全局关闭）。"""
    if not exec_test_result:
        return True
    mode = exec_test_result.get("execution_mode", "")
    if mode == "skipped":
        return True
    if mode == "error":
        return False
    return int(exec_test_result.get("total") or 0) == 0


def _module_clear_to_pass(
    review_passed: bool,
    exec_test_result: dict[str, Any] | None,
) -> bool:
    """审查通过且（测试全过或未跑真实测试）才可标记 passed。"""
    if not review_passed:
        return False
    return _exec_tests_passed(exec_test_result) or _exec_tests_skipped(exec_test_result)


def _apply_mvp_module_cap(
    modules: list[dict[str, Any]],
    state: WorkflowState,
) -> list[dict[str, Any]]:
    cap = _resolve_mvp_module_cap(state)
    if cap is None or len(modules) <= cap:
        return modules
    return modules[:cap]


def _sanitize_business_multi_modules(
    modules: list[dict[str, Any]],
    state: WorkflowState,
) -> list[dict[str, Any]]:
    """商业 cap>1 时强制 backend 轻量模块，避免 PM 产出前端/ML 导致全 blocked。"""
    cap = _resolve_mvp_module_cap(state)
    alignment = state.get("alignment_result") or {}
    if (
        cap is None
        or cap <= 1
        or not isinstance(alignment, dict)
        or alignment.get("plan_type") != "business"
    ):
        return modules

    requirement = (state.get("requirement") or "").strip()
    sanitized: list[dict[str, Any]] = []
    for i, m in enumerate(modules[:cap]):
        name = m.get("module_name") or m.get("module") or f"business_mvp_{i + 1}"
        pm_hint = (m.get("description") or "")[:100]
        sanitized.append({
            "module_name": name,
            "description": (
                f"商业 MVP 后端模块 {i + 1}/{cap}：实现一项小型可测试 Python 能力。"
                f"需求上下文：{requirement[:350]}。"
                f"（PM 摘要：{pm_hint}）"
                "单文件≤120行，仅标准库+pytest，不要前端/ML/SQLite。"
            ),
            "dependencies": [],
            "type": "backend",
        })
    return sanitized if sanitized else modules


