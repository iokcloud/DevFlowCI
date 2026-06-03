"""工作流运行器 — 后台任务管理、工作流触发、状态持久化。

从 main.py 提取，使 api/ 模块可独立引用。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone

UTC = timezone.utc
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select as _sel

from config import (
    AUTOPILOT_ENABLED,
    BUSINESS_MVP_MAX_MODULES,
    DELIVERIES_DIR,
    MAX_HUMAN_FIXES,
    MEMORY_DIR,
    PROJECT_ROOT,
)
from database.db import async_session_factory
from database.models import (
    ModuleStatus,
    ModuleTask,
    Project,
    ProjectLog,
    ProjectStatus,
)
from workflow.executor import WorkflowExecutor, WorkflowState
from workflow.sse_bridge import push_log, push_project_snapshot, remove_log_queue

logger = logging.getLogger(__name__)

# ── 后台任务追踪（用于终止）──────────────────────────



# ── 执行器引用（由 main.py 设置）────────────────────────

_executor: WorkflowExecutor | None = None

def set_workflow_executor(executor: WorkflowExecutor) -> None:
    global _executor
    _executor = executor

# ── 后台任务追踪（用于终止）──────────────────────────
_running_tasks: dict[str, asyncio.Task] = {}


def _register_task(project_id: str, coro) -> None:
    """注册后台任务，支持手动终止。"""
    async def _wrapper():
        try:
            await coro
        finally:
            _running_tasks.pop(project_id, None)
    task = asyncio.create_task(_wrapper())
    _running_tasks[project_id] = task


def _resolve_mvp_max_modules_from_project(
    project: Project,
    alignment_result: dict[str, Any],
) -> int | None:
    """从 alignment / 商业规则解析 MVP 模块上限。"""
    stored = alignment_result.get("mvp_max_modules")
    if stored is not None:
        try:
            return max(1, int(stored))
        except (TypeError, ValueError):
            pass
    if alignment_result.get("plan_type") == "business":
        _, cap = _build_business_tech_requirement(
            project.requirement, alignment_result
        )
        return cap
    return None



# ── 后台工作流执行 ────────────────────────────────────────

def _build_business_tech_requirement(
    original_requirement: str,
    alignment_result: dict[str, Any],
) -> tuple[str, int]:
    """商业计划确认后生成技术需求与 MVP 模块上限。

    若原始需求含 is_prime/素数 等单函数 MVP 提示，走与技术冒烟相同的单模块路径。
    当 ``BUSINESS_MVP_MAX_MODULES > 1`` 时，按 Phase-1 多项 action 生成多模块技术需求。
    """
    lower = original_requirement.lower()
    if "is_prime" in lower or "素数" in original_requirement:
        return (
            "Write a Python function is_prime(n: int) -> bool that checks if a "
            "number is prime. Include type hints and docstring. Single module only.",
            1,
        )

    exec_summary = alignment_result.get("executive_summary", "")
    recommendations = alignment_result.get("recommendations", "")
    roadmap = alignment_result.get("roadmap", [])
    cap_limit = BUSINESS_MVP_MAX_MODULES

    if cap_limit > 1 and (roadmap or recommendations or exec_summary):
        return _build_business_multi_module_requirement(
            exec_summary, recommendations, roadmap, cap_limit,
        )

    # cap=1：Phase-1 单模块 MVP
    if roadmap:
        first = roadmap[0]
        phase_name = first.get("phase", "第一阶段")
        actions = first.get("actions") or []
        milestone = (first.get("milestones") or [""])[0]
        action_text = actions[0] if actions else (recommendations or exec_summary or "核心功能原型")
        tech_requirement = (
            f"商业计划「{phase_name}」技术 MVP：{action_text}。"
            f"{f'里程碑：{milestone}。' if milestone else ''}"
            "请实现为一个可运行的 Python 模块（含 type hints、docstring、pytest 单元测试）。"
            "仅一个模块，不要拆分。"
            "范围：单文件≤120行，只实现上述第一项核心能力，禁止 SQLite/CLI/多子系统。"
        )
        if exec_summary:
            tech_requirement = f"背景：{exec_summary[:300]}。{tech_requirement}"
        return tech_requirement, 1

    if recommendations or exec_summary:
        focus = recommendations or exec_summary
        tech_requirement = (
            f"商业计划技术 MVP（单模块）：{focus[:500]}。"
            "请实现为一个可运行的 Python 模块（含 type hints、docstring、pytest 单元测试）。"
            "仅一个模块，不要拆分。"
            "范围：单文件≤120行，只实现上述第一项核心能力，禁止 SQLite/CLI/多子系统。"
        )
        return tech_requirement, 1

    parts: list[str] = []
    if exec_summary:
        parts.append(f"商业计划摘要：{exec_summary}")
    tech_requirement = "。".join(parts) if parts else original_requirement
    tech_requirement += (
        f"。【MVP约束】本次仅实现最多 {BUSINESS_MVP_MAX_MODULES} 个核心模块，"
        "优先最小可行产品，避免过度拆分。"
    )
    return tech_requirement, BUSINESS_MVP_MAX_MODULES


def _build_business_multi_module_requirement(
    exec_summary: str,
    recommendations: str,
    roadmap: list[dict[str, Any]],
    cap_limit: int,
) -> tuple[str, int]:
    """BUSINESS_MVP_MAX_MODULES>1 时，从 Phase-1 提取最多 cap 项能力作为多模块 MVP。"""
    first = roadmap[0] if roadmap else {}
    phase_name = first.get("phase", "第一阶段")
    actions: list[str] = list(first.get("actions") or [])
    if not actions and recommendations:
        actions = [recommendations[:200]]
    if not actions and exec_summary:
        actions = [exec_summary[:200]]
    while len(actions) < cap_limit:
        actions.append(f"核心能力子模块 {len(actions) + 1}")
    actions = actions[:cap_limit]

    items = "；".join(f"模块{i + 1}「{a}」" for i, a in enumerate(actions))
    tech_requirement = (
        f"商业计划「{phase_name}」技术 MVP（最多 {cap_limit} 个独立 Python 模块）：{items}。"
        "每个模块均为 backend 类型：单文件≤120行的纯 Python 函数或类，含 type hints、docstring、pytest；"
        "禁止 frontend/微信小程序/ML 训练/SQLite/CLI；模块间松耦合、无依赖优先。"
    )
    if exec_summary:
        tech_requirement = f"背景：{exec_summary[:300]}。{tech_requirement}"
    return tech_requirement, cap_limit


def _record_alignment_to_decisions(project_id: str, alignment_json_str: str | None) -> None:
    """将对齐产生的假设和风险记录到 decisions.md（简化记录）。"""
    if not alignment_json_str:
        return
    try:
        alignment = json.loads(alignment_json_str)
    except (json.JSONDecodeError, TypeError):
        return

    assumptions = alignment.get("assumptions", [])
    risks = alignment.get("risks", [])

    if not assumptions and not risks:
        return

    decisions_path = PROJECT_ROOT / "docs" / "decisions.md"
    timestamp = datetime.now(UTC).isoformat()

    entry_lines = [
        "",
        f"## 对齐记录 — {project_id}（{timestamp}）",
        "",
    ]
    if assumptions:
        entry_lines.append("### 假设")
        for a in assumptions:
            entry_lines.append(f"- {a}")
        entry_lines.append("")
    if risks:
        entry_lines.append("### 风险")
        for r in risks:
            entry_lines.append(f"- {r}")
        entry_lines.append("")

    try:
        with open(decisions_path, "a", encoding="utf-8") as f:
            f.write("\n".join(entry_lines))
    except Exception as exc:
        logger.warning("写入 decisions.md 失败: %s", exc)
        pass


def _write_delivery_plan(project_id: str, project: Any) -> None:
    """将用户确认后的最终执行计划写入 DELIVERY_PLAN.md（根目录兼容副本）。"""
    from workflow.document_sync import ctx_from_project, sync_delivery_plan

    alignment = None
    if project.alignment_json:
        try:
            alignment = json.loads(project.alignment_json)
        except (json.JSONDecodeError, TypeError):
            alignment = None
    ctx = ctx_from_project(project, alignment=alignment)
    sync_delivery_plan(ctx, event="alignment_confirmed")
    # 根目录保留一份指针，便于早期工具链
    docs_plan = DELIVERIES_DIR / project_id / "docs" / "DELIVERY_PLAN.md"
    legacy = DELIVERIES_DIR / project_id / "DELIVERY_PLAN.md"
    try:
        legacy.parent.mkdir(parents=True, exist_ok=True)
        if docs_plan.is_file():
            legacy.write_text(
                docs_plan.read_text(encoding="utf-8"), encoding="utf-8"
            )
    except OSError:
        pass


async def _run_workflow_after_alignment(
    project_id: str, state: WorkflowState
) -> None:
    """对齐确认后，继续执行：上下文分析 → 规划 → 等待 plan_ready 确认。"""
    import traceback as _tb
    final_state: WorkflowState | None = None
    try:
        final_state = await _executor.execute_after_alignment(state)
        await _save_state(project_id, final_state)
    except Exception as exc:
        err_msg = f"工作流异常: {exc}"
        await push_log(project_id, "ERROR", err_msg)
        await push_log(project_id, "ERROR", _tb.format_exc())
        try:
            state["status"] = "needs_review"
            state.setdefault("errors", []).append(err_msg)
            await _save_state(project_id, state)
        except Exception as exc:
            logger.warning("异常时保存状态失败: %s", exc)
            pass
    finally:
        await asyncio.sleep(5)
        remove_log_queue(project_id)


async def _save_state(project_id: str, final_state: WorkflowState) -> None:
    """持久化工作流状态到数据库。"""
    from sqlalchemy import select


    async with async_session_factory() as db:
        result = await db.execute(
            select(Project).where(Project.project_id == project_id)
        )
        project = result.scalar_one_or_none()
        if not project:
            return
        status_map = {
            "completed": ProjectStatus.COMPLETED,
            "completed_with_warnings": ProjectStatus.COMPLETED,
            "failed": ProjectStatus.FAILED,
            "aligning": ProjectStatus.ALIGNING,
            "aligned": ProjectStatus.ALIGNED,
            "plan_ready": ProjectStatus.PLAN_READY,
            "planning": ProjectStatus.PLANNING,
            "executing": ProjectStatus.EXECUTING,
            "integrating": ProjectStatus.INTEGRATING,
            "reviewing": ProjectStatus.REVIEWING,
            "needs_review": ProjectStatus.NEEDS_REVIEW,
            "cancelled": ProjectStatus.CANCELLED,
            "finalized": ProjectStatus.FINALIZED,
        }
        project.status = status_map.get(
            final_state.get("status", "failed"), ProjectStatus.FAILED
        )
        if final_state.get("plan_json"):
            project.plan_json = final_state["plan_json"]
        # 保存对齐分析结果
        if final_state.get("alignment_result"):
            project.alignment_json = json.dumps(
                final_state["alignment_result"], ensure_ascii=False
            )
        project.delivery_path = final_state.get("delivery_path", "")
        project.test_report_path = final_state.get("test_report_path", "")
        project.blocked_count = len(final_state.get("blocked_modules", []))
        if final_state.get("context_scan"):
            project.context_scan_json = json.dumps(
                final_state["context_scan"], ensure_ascii=False
            )

        review = final_state.get("global_review", {})
        if review:
            project.final_report = json.dumps(review, ensure_ascii=False)
        if final_state.get("iteration") is not None:
            project.iteration = int(final_state["iteration"])
        for module_name, result_data in final_state.get(
            "module_results", {}
        ).items():
            mod_result = await db.execute(
                select(ModuleTask).where(
                    ModuleTask.project_id_fk == project.id,
                    ModuleTask.module_name == module_name,
                )
            )
            mod = mod_result.scalar_one_or_none()
            if mod:
                status_str = result_data.get("status", "failed")
                mod.status = ModuleStatus(status_str)
                mod.code = result_data.get("code", "")
                mod.tests = result_data.get("test_code", "")
                mod.spec = json.dumps(
                    result_data.get("spec", {}), ensure_ascii=False
                )
                mod.retry_count = result_data.get("retry_count", 0)
                mod.failure_reason = result_data.get("failure_reason", "")
                db.add(mod)
        await db.commit()


async def _run_improvement(
    project_id: str,
    state: WorkflowState,
    target_modules: list[str],
    *,
    reintegrate_only: bool = False,
) -> None:
    """后台：迭代改进（补跑模块 → 集成 → 审查 → 打包）。"""
    import traceback as _tb

    final_state = None
    try:
        final_state = await _executor.execute_improvement(
            state,
            target_modules,
            reintegrate_only=reintegrate_only,
        )
        if final_state.get("iteration") is None:
            final_state["iteration"] = state.get("iteration", 1)
        await _save_state(project_id, final_state)
    except Exception as exc:
        err_msg = f"迭代改进异常: {exc}"
        await push_log(project_id, "ERROR", err_msg)
        await push_log(project_id, "ERROR", _tb.format_exc())
        try:
            state["status"] = "needs_review"
            state.setdefault("errors", []).append(err_msg)
            await _save_state(project_id, state)
        except Exception as exc:
            logger.warning("异常时保存状态失败: %s", exc)
            pass
    finally:
        await asyncio.sleep(5)
        remove_log_queue(project_id)


async def _run_workflow(
    project_id: str, state: WorkflowState
) -> None:
    """后台执行完整工作流（统一项目开发模式）。"""
    import traceback as _tb
    final_state = None  # 初始化，避免 finally 中 UnboundLocalError
    try:
        final_state = await _executor.execute(state)
        await _save_state(project_id, final_state)
    except Exception as exc:
        # ★ 修复：不再静默吞异常，记录错误到日志并将项目标记为失败
        err_msg = f"工作流异常: {exc}"
        await push_log(project_id, "ERROR", err_msg)
        await push_log(project_id, "ERROR", _tb.format_exc())
        # 将项目状态更新为 needs_review，前端可据此展示错误
        try:
            state["status"] = "needs_review"
            state.setdefault("errors", []).append(err_msg)
            await _save_state(project_id, state)
        except Exception as exc:
            logger.warning("异常时保存状态失败: %s", exc)
            pass
    finally:
        # ★ 对齐完成后不移除日志队列，等待用户确认后继续使用
        if final_state is None or final_state.get("status") != "aligned":
            await asyncio.sleep(5)
            remove_log_queue(project_id)


async def _run_execution(project_id: str, state: WorkflowState) -> None:
    """后台：confirm_plan 后继续执行剩余阶段。"""
    import traceback as _tb
    final_state: WorkflowState | None = None
    try:
        final_state = await _executor.execute_from_plan(state)
        await _save_state(project_id, final_state)
    except Exception as exc:
        err_msg = f"执行阶段异常: {exc}"
        await push_log(project_id, "ERROR", err_msg)
        await push_log(project_id, "ERROR", _tb.format_exc())
        try:
            state["status"] = "needs_review"
            state.setdefault("errors", []).append(err_msg)
            await _save_state(project_id, state)
        except Exception:
            pass
    finally:
        await asyncio.sleep(5)
        remove_log_queue(project_id)


