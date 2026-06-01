"""LangGraph 工作流定义。

定义 DevFlow CI 的主执行图和模块子图：
- 主图：上下文分析（可选）→ PM 规划 → 并行模块 → 集成 → 全局审查 → 打包
- 子图：分析 → 编码 → 测试 → 审查（最多 MAX_REVIEW_RETRIES 次重试）
"""

from __future__ import annotations

import json
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph


# ── State Schema ──────────────────────────────────────────

class WorkflowState(TypedDict, total=False):
    """全局工作流状态。

    通过 LangGraph 节点间传递，每次节点返回部分更新。
    """

    # ── 输入 ──
    project_id: str                     # 项目标识
    requirement: str                    # 用户原始需求
    directory: str                      # 项目目录路径（可选）
    project_context: str                # 上下文分析结果 / 全局上下文（技术栈等）

    # ── 需求对齐阶段 ──
    alignment_result: dict[str, Any]    # AlignmentAgent 产出的对齐分析结果
    alternative_alignment: dict[str, Any] | None  # 备选对齐方案（多方案时）

    # ── 规划阶段 ──
    plan_json: str                      # PM 产出的 JSON 字符串
    plan_modules: list[dict[str, Any]]  # 解析后的模块列表

    # ── 模块执行阶段 ──
    module_results: dict[str, dict[str, Any]]  # module_name → 执行结果
    module_order: list[str]             # 拓扑排序后的模块执行顺序
    blocked_modules: list[str]          # 被阻塞的模块名称列表

    # ── 集成阶段 ──
    integration_result: dict[str, Any]  # 集成产出

    # ── 审查阶段 ──
    global_review: dict[str, Any]       # 全局审查结果

    # ── 交付阶段 ──
    delivery_path: str                  # ZIP 文件路径

    # ── 控制 ──
    status: str                         # 项目状态
    errors: list[str]                   # 全局错误


class ModuleState(TypedDict, total=False):
    """单模块子图状态。"""

    module_name: str
    description: str
    project_context: str
    spec: dict[str, Any]                # ModuleSpec 序列化
    code: dict[str, Any]                # ModuleCode 序列化
    test_result: dict[str, Any]         # ModuleTestResult 序列化
    review_result: dict[str, Any]       # ReviewResult 序列化
    retry_count: int
    max_retries: int
    failure_reason: str                 # 阻塞原因
    status: str
    error: str


# ── 主图构建工厂 ──────────────────────────────────────────

def build_main_graph() -> StateGraph:
    """构建 DevFlow CI 主工作流图。

    图结构：
        START → [context_analysis] → plan → [parallel modules] → integrate → global_review → package → END

    注意：图节点的实际逻辑在 executor.py 中实现。
    本函数只定义图的拓扑结构（节点和边）。
    """
    builder = StateGraph(WorkflowState)

    # ── 注册节点 ──
    builder.add_node("requirement_alignment", _requirement_alignment_node)
    builder.add_node("context_analysis", _context_analysis_node)
    builder.add_node("plan", _plan_node)
    builder.add_node("dispatch_modules", _dispatch_modules_node)
    builder.add_node("integrate", _integrate_node)
    builder.add_node("global_review", _global_review_node)
    builder.add_node("package", _package_node)

    # ── 边 ──
    # 从 requirement_alignment 开始（强制执行需求对齐）
    builder.set_entry_point("requirement_alignment")
    builder.add_edge("requirement_alignment", "context_analysis")
    builder.add_edge("context_analysis", "plan")
    builder.add_edge("plan", "dispatch_modules")
    builder.add_conditional_edges(
        "dispatch_modules", _after_modules, {
            "integrate": "integrate",
            "failed": END,
        }
    )
    builder.add_edge("integrate", "global_review")
    builder.add_conditional_edges(
        "global_review", _after_global_review, {
            "package": "package",
            "failed": END,
        }
    )
    builder.add_edge("package", END)

    return builder


def build_module_subgraph() -> StateGraph:
    """构建模块子图：分析 → 编码 → 测试 → 审查（可重试）。

    Returns:
        StateGraph 对象，需要后续编译
    """
    builder = StateGraph(ModuleState)

    builder.add_node("analyze", _analyze_node)
    builder.add_node("code", _code_node)
    builder.add_node("test", _test_node)
    builder.add_node("review", _review_node)

    builder.set_entry_point("analyze")
    builder.add_edge("analyze", "code")
    builder.add_edge("code", "test")
    builder.add_edge("test", "review")
    builder.add_conditional_edges(
        "review", _after_review, {
            "code": "code",      # 重试：回到编码
            "passed": END,
            "failed": END,
        }
    )

    return builder


# ── 节点占位函数（实际逻辑在 executor 中通过 add_node 注入） ──

async def _requirement_alignment_node(state: WorkflowState) -> dict[str, Any]:
    """占位：需求对齐节点。由 executor 覆写。"""
    return {}

async def _context_analysis_node(state: WorkflowState) -> dict[str, Any]:
    """占位：上下文分析节点。由 executor 覆写。"""
    return {}


async def _plan_node(state: WorkflowState) -> dict[str, Any]:
    """占位：PM 规划节点。由 executor 覆写。"""
    return {"status": state.get("status", "planning")}


async def _dispatch_modules_node(state: WorkflowState) -> dict[str, Any]:
    """占位：模块调度节点。由 executor 覆写。"""
    return {}


async def _integrate_node(state: WorkflowState) -> dict[str, Any]:
    """占位：集成节点。由 executor 覆写。"""
    return {}


async def _global_review_node(state: WorkflowState) -> dict[str, Any]:
    """占位：全局审查节点。由 executor 覆写。"""
    return {}


async def _package_node(state: WorkflowState) -> dict[str, Any]:
    """占位：打包节点。由 executor 覆写。"""
    return {}


async def _analyze_node(state: ModuleState) -> dict[str, Any]:
    """占位：分析节点。由 executor 覆写。"""
    return {}


async def _code_node(state: ModuleState) -> dict[str, Any]:
    """占位：编码节点。由 executor 覆写。"""
    return {}


async def _test_node(state: ModuleState) -> dict[str, Any]:
    """占位：测试节点。由 executor 覆写。"""
    return {}


async def _review_node(state: ModuleState) -> dict[str, Any]:
    """占位：审查节点。由 executor 覆写。"""
    return {}


# ── 条件路由函数 ─────────────────────────────────────────

def _after_modules(state: WorkflowState) -> str:
    """模块执行完后的路由。

    所有模块通过或有 blocked 模块也可以进入集成（永不卡死策略），
    仅当所有模块都失败时才停止。
    """
    module_results = state.get("module_results", {})
    if not module_results:
        return "failed"

    # 检查是否有任何非失败的模块（passed 或 blocked）
    has_viable = any(
        r.get("status") in ("passed", "blocked")
        for r in module_results.values()
    )
    return "integrate" if has_viable else "failed"


def _after_global_review(state: WorkflowState) -> str:
    """全局审查后的路由。"""
    review = state.get("global_review", {})
    if review.get("passed", False):
        return "package"
    # 分数 > 50 仍可交付带建议
    if review.get("score", 0) > 50:
        return "package"
    return "failed"


def _after_review(state: ModuleState) -> str:
    """模块审查后的路由。"""
    review = state.get("review_result", {})
    if review.get("passed", False):
        return "passed"
    retry = state.get("retry_count", 0)
    max_retries = state.get("max_retries", 5)
    if retry < max_retries:
        return "code"  # 回到编码重试
    return "failed"


# ── 辅助函数 ──────────────────────────────────────────────

def topological_sort(modules: list[dict[str, Any]]) -> list[str]:
    """对模块列表按依赖关系做拓扑排序。

    无依赖的模块在前，被依赖的模块在前。

    Args:
        modules: PM 产出的模块列表

    Returns:
        排序后的模块名称列表
    """
    # 构建邻接表
    name_to_idx = {m["module_name"]: i for i, m in enumerate(modules)}
    in_degree = {m["module_name"]: 0 for m in modules}
    children = {m["module_name"]: [] for m in modules}

    for m in modules:
        for dep in m.get("dependencies", []):
            if dep in name_to_idx:
                in_degree[m["module_name"]] += 1
                children[dep].append(m["module_name"])

    # Kahn 算法
    from collections import deque
    queue = deque(
        name for name, deg in in_degree.items() if deg == 0
    )
    result: list[str] = []

    while queue:
        node = queue.popleft()
        result.append(node)
        for child in children[node]:
            in_degree[child] -= 1
            if in_degree[child] == 0:
                queue.append(child)

    # 若存在未处理的节点（循环依赖），追加到末尾
    for name in in_degree:
        if name not in result:
            result.append(name)

    return result
