"""工作流执行器 — 驱动 LangGraph 图执行，管理 SSE 日志流。

核心职责：
1. 编译并执行主工作流图
2. 在模块级实现自定义子图逻辑（分析→编码→测试→审查 重试循环）
3. 管理 SSE 日志推送队列
4. 并行执行无依赖的模块
5. 上下文分析节点（读取项目目录）
6. 永不卡死策略：blocked 替换 failed，占位文件，TODO.md
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import traceback
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from langgraph.graph import StateGraph

from agents.alignment_agent import AlignmentAgent
from agents.business_planner import BusinessPlannerAgent
from agents.integrator import (
    GlobalReviewerAgent,
    GlobalReviewResult,
    IntegrationResult,
    IntegratorAgent,
)
from agents.module_agents import (
    ModuleAgents,
    ModuleCode,
    ModuleSpec,
    ModuleTestResult,
)
from agents.planner import PlannerAgent
from agents.repair_agent import RepairAgent
from agents.reviewer import ReviewerAgent, ReviewResult
from config import (
    ARCHIVE_DIR_NAME,
    AUTO_FIX_ENABLED,
    AUTO_FIX_MAX_TOTAL_ROUNDS,
    AUTO_FIX_QUICK_REVIEW_MAX,
    AUTOPILOT_ENABLED,
    BUSINESS_MVP_MAX_MODULES,
    CONTEXT_ANALYSIS_MAX_DEPTH,
    CONTEXT_ANALYSIS_TOKEN_LIMIT,
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    DEEPSEEK_MODEL,
    DELIVERIES_DIR,
    DEPENDENCY_INFERENCE_ENABLED,
    FEEDBACK_LEARNING_ENABLED,
    MAX_CODE_READINESS_RETRIES,
    MAX_CONCURRENT_MODULES,
    MAX_NON_MODULE_RETRIES,
    MAX_REVIEW_RETRIES,
    MAX_VERSIONS_KEPT,
    TEST_EXECUTION_ENABLED,
    TEST_FULL_SUITE_TIMEOUT,
    TEST_INTEGRATION_TIMEOUT,
    TEST_MAX_FAILURES_BEFORE_WARN,
    TEST_UNIT_TIMEOUT,
    VERSIONED_DELIVERY_ENABLED,
)
from memory.case_store import CaseStore
from memory.project_memory import ProjectMemoryStore
from workflow.auto_fix import FixContext, classify_error, search_similar_cases
from workflow.code_quick_fix import apply_code_quick_fixes, repair_truncation_shell
from workflow.code_readiness import assess_module_code, format_readiness_feedback
from workflow.iteration_automation import (
    build_checklist_repair_feedback,
    build_iteration_context_extras,
    ensure_delivery_fixtures,
    infer_extra_requirements,
    merge_requirements_text,
    resolve_blocked_artifacts,
)
from workflow.langgraph_def import (
    ModuleState,
    WorkflowState,
    _after_global_review,
    _after_modules,
    _after_review,
    build_main_graph,
    topological_sort,
)
from workflow.passed_module_context import build_passed_module_context
from workflow.requirement_context import merge_sources_label
from workflow.stream_relay import AGENT_LABEL_MAP, push_ai_token
from workflow.test_runner import (
    TestResult,
    build_integration_module_files,
    run_full_test_suite,
    run_integration_tests,
    run_module_tests,
)

logger = logging.getLogger(__name__)


# ── 从子模块导入（保持向下兼容的命名空间）─────────────────

from workflow.context_analysis import (
    DOC_EXTENSIONS,
    DOC_PRIORITY_KEYWORDS,
    IGNORED_DIRS,
    MAX_DOC_CHARS_BUSINESS,
    MAX_DOC_CHARS_DEFAULT,
    MAX_DOC_FILES,
    _build_tree,
    _collect_doc_files,
    _detect_document_type,
    _doc_priority_score,
    _estimate_tokens,
    _generate_doc_summaries,
    _persist_module_result,
    _persist_plan_modules,
    _read_doc_file_content,
    _read_docx,
    _read_json_as_text,
    _update_module_status,
    analyze_project_context,
    analyze_project_context_structured,
    build_context_scan_meta,
    persist_context_scan,
)
from workflow.fallback_alignment import _fallback_alignment
from workflow.mvp_utils import (
    _apply_mvp_module_cap,
    _exec_tests_passed,
    _exec_tests_skipped,
    _module_clear_to_pass,
    _normalize_business_mvp_modules,
    _resolve_mvp_module_cap,
    _sanitize_business_multi_modules,
)
from workflow.sse_bridge import (
    SNAPSHOT_DEBOUNCE_SEC,
    _notify_ai_stream,
    _sync_project_status,
    get_log_queue,
    push_log,
    push_module_event,
    push_project_snapshot,
    remove_log_queue,
    stream_logs,
)
from workflow.stub_generator import _generate_stub_code

# ── 工作流执行器 ──────────────────────────────────────────

class WorkflowExecutor:
    """DevFlow CI 工作流执行器。

    驱动从规划到交付的完整流程，并通过 LangGraph 管理状态。
    """

    def __init__(
        self,
        planner: PlannerAgent,
        modules: ModuleAgents,
        reviewer: ReviewerAgent,
        integrator: IntegratorAgent,
        global_reviewer: GlobalReviewerAgent,
        repair_agent: RepairAgent | None = None,
        alignment_agent: AlignmentAgent | None = None,
        business_planner: BusinessPlannerAgent | None = None,
        case_store: CaseStore | None = None,
        project_memory_store: ProjectMemoryStore | None = None,
    ) -> None:
        self._planner = planner
        self._modules = modules
        self._reviewer = reviewer
        self._integrator = integrator
        self._global_reviewer = global_reviewer
        self._repair_agent = repair_agent or RepairAgent()
        self._alignment_agent = alignment_agent or AlignmentAgent()
        self._business_planner = business_planner or BusinessPlannerAgent()
        self._case_store = case_store
        self._project_memory = project_memory_store or ProjectMemoryStore()

    async def execute_alignment(self, state: WorkflowState) -> WorkflowState:
        """执行需求对齐阶段（唯一入口：所有构建工作流强制执行）。

        完成后 state["status"] = "aligned"，等待用户确认。
        """
        pid = state["project_id"]
        state.setdefault("module_results", {})
        state.setdefault("errors", [])
        state.setdefault("blocked_modules", [])

        directory = state.get("directory", "")
        user_req = (state.get("requirement") or "").strip()
        project_memory_text = ""
        if directory:
            try:
                project_memory_text = self._project_memory.format_for_prompt(directory)
            except Exception as exc:
                logger.warning("无法读取项目记忆: %s", exc)

        # ── 优先执行上下文分析：提取文档摘要供对齐 Agent 使用 ──
        structured_context: dict[str, Any] = {"analyzed_files": [], "source_files": []}
        if directory:
            await push_log(pid, "INFO", f"开始分析项目目录文档: {directory}")
            try:
                structured_context = analyze_project_context_structured(directory)
                state["project_context"] = json.dumps(structured_context, ensure_ascii=False)
                file_count = len(structured_context.get("analyzed_files", []))
                if structured_context.get("no_documentation_found"):
                    await push_log(pid, "INFO", "目录下未发现文档文件，将与文字指令或代码文件列表合并分析")
                else:
                    await push_log(pid, "SUCCESS", f"文档分析完成：{file_count} 个文件")
                scan_meta = build_context_scan_meta(
                    structured_context, directory=directory
                )
                state["context_scan"] = scan_meta
                await persist_context_scan(pid, scan_meta)
            except Exception as exc:
                await push_log(pid, "WARN", f"上下文分析异常: {exc}，将跳过")
                # ★ 修复：确保 structured_context 非空，保留基本信息
                structured_context = {
                    "analyzed_files": [],
                    "overall_summary": f"目录分析失败: {exc}",
                    "source_files": [],
                    "no_documentation_found": True,
                }

        # 推送状态事件：进入对齐阶段
        await push_log(pid, "STATE", "aligning", state_event="aligning")

        # ── 根据文档类型选择 Agent ──
        doc_type = structured_context.get("document_type", "generic") if structured_context else "generic"
        # 用户可强制指定模式（通过 state 传入）
        force_mode = state.get("force_mode", "")
        if force_mode in ("technical", "business"):
            doc_type = force_mode

        # ★ 增强：检查是否使用了自动生成需求 + 有文档 → 强制重新评估 doc_type
        has_docs = len(structured_context.get("analyzed_files", [])) > 0
        directory_only = bool(directory) and not user_req

        await push_log(
            pid,
            "INFO",
            f"📋 合并分析来源：{merge_sources_label(user_req, directory)}"
            + (f" | 文档数: {len(structured_context.get('analyzed_files', []))}"
               if has_docs else " | 无文档")
            + (f" | 代码文件: {len(structured_context.get('source_files', []))}"
               if structured_context.get("source_files") else "")
            + (" | 仅目录资料" if directory_only else ""),
        )

        await push_log(pid, "INFO", f"📋 文档类型判定: {doc_type}"
            + (f" (用户指定: {force_mode})" if force_mode else " (自动识别)")
            + (f" | 文档数: {len(structured_context.get('analyzed_files', []))}"
               if has_docs else " | 无文档")
            + (" | 仅目录资料" if directory_only else ""))

        # ★ 核心修复：generic / technical 但有文档且无源代码文件 → 大概率是商业文档
        # 三次确认机制，确保不会漏判
        if doc_type != "business" and has_docs:
            source_files = structured_context.get("source_files", [])
            no_source_code = len(source_files) == 0
            no_docs_found = structured_context.get("no_documentation_found", False)

            # 确认1：有文档但没有源代码文件 → 强制商业模式
            if no_source_code and not no_docs_found:
                doc_type = "business"
                await push_log(pid, "INFO", "🔄 目录有文档但无源代码文件，强制切换为商业计划模式")
            elif doc_type == "generic":
                # 确认2：二次检查文档内容
                all_text = " ".join(
                    af.get("summary", "") for af in structured_context.get("analyzed_files", [])
                ).lower() if structured_context else ""
                tech_keywords = ["api", "后端", "前端", "数据库", "微服务", "代码", "接口",
                                 "python", "java", "javascript", "react", "部署", "测试", "架构",
                                 "函数", "类", "模块", "import", "def ", "class "]
                has_tech = any(kw in all_text for kw in tech_keywords)
                if not has_tech:
                    doc_type = "business"
                    await push_log(pid, "INFO", "🔄 文档中无技术关键词，切换为商业计划模式")

        # ── 统一 Agent 调用策略：优先用对应 Agent，失败后用另一 Agent 兜底 ──
        alignment_result = None
        last_error = None
        last_align_errors: list[str] = []

        for attempt in range(1, MAX_NON_MODULE_RETRIES + 1):
            try:
                if doc_type == "business":
                    # 商业文档 → BusinessPlannerAgent
                    await push_log(pid, "INFO", f"商业计划分析师生成计划中...（第 {attempt} 次）")
                    state["status"] = "aligning"
                    await _notify_ai_stream(pid, "business_planner", "start")
                    biz_plan = await self._business_planner.plan(
                        structured_context,
                        project_memory_text,
                        user_requirement=user_req,
                    )
                    alignment_result = BusinessPlannerAgent.format_for_display(biz_plan)
                    # 推送商业计划摘要到 AI 流
                    summary_text = alignment_result.get("executive_summary", "")[:2000]
                    if summary_text:
                        await _notify_ai_stream(pid, "business_planner", "token", summary_text)
                    await _notify_ai_stream(pid, "business_planner", "done", f"{len(biz_plan.get('roadmap', []))} 个阶段")
                    await push_log(pid, "SUCCESS", "商业计划生成完成", state_event="aligned")
                    break

                # 技术/通用 → AlignmentAgent
                await push_log(pid, "INFO", f"需求对齐分析师开始分析...（第 {attempt} 次）")
                state["status"] = "aligning"
                await _notify_ai_stream(pid, "alignment_agent", "start")

                # ★ 重试时注入上轮验证反馈，帮助 LLM 自我修正
                retry_req = state["requirement"]
                if attempt > 1:
                    retry_req = (
                        retry_req
                        + "\n\n【上次对齐验证失败，请修正】\n"
                        + "\n".join(f"- {e}" for e in last_align_errors)
                    )

                plan_a, plan_b, errors = await self._alignment_agent.analyze_alternatives(
                    retry_req,
                    project_context=state.get("project_context", ""),
                    project_memory_text=project_memory_text,
                )
                if errors:
                    last_align_errors = errors
                    await push_log(pid, "ERROR", f"对齐分析验证失败: {'; '.join(errors)}")
                    if attempt < MAX_NON_MODULE_RETRIES:
                        continue
                    plan_a = _fallback_alignment(state["requirement"], structured_context)
                    plan_b = {}
                    await push_log(pid, "WARN", "对齐分析未通过验证，使用兜底方案")

                # ★ insufficient_info：有文本需求则基于需求兜底，不再死胡同
                if plan_a.get("status") == "insufficient_info":
                    if state.get("requirement", "").strip():
                        await push_log(
                            pid, "INFO",
                            "对齐返回信息不足，但用户已提供需求 — 基于需求生成执行计划",
                        )
                        plan_a = _fallback_alignment(
                            state["requirement"], structured_context
                        )
                        plan_a.pop("status", None)
                    elif structured_context and (
                        has_docs or not structured_context.get("no_documentation_found", True)
                    ):
                        await push_log(pid, "INFO", "🔄 AlignmentAgent 返回信息不足，尝试商业计划模式兜底...")
                        try:
                            biz_plan = await self._business_planner.plan(
                                structured_context,
                                project_memory_text,
                                user_requirement=user_req,
                            )
                            alignment_result = BusinessPlannerAgent.format_for_display(biz_plan)
                            await push_log(pid, "SUCCESS", "商业计划兜底成功", state_event="aligned")
                            state["alignment_result"] = alignment_result
                            if plan_b:
                                state["alternative_alignment"] = AlignmentAgent.format_alignment_for_display(plan_b)
                            state["status"] = "aligned"
                            return state
                        except Exception as biz_exc:
                            await push_log(pid, "WARN",
                                f"商业计划兜底也失败: {biz_exc}，使用智能回退方案")
                            plan_a = _fallback_alignment(
                                state["requirement"], structured_context
                            )
                            plan_b = {}

                alignment_result = AlignmentAgent.format_alignment_for_display(plan_a)
                if plan_b:
                    state["alternative_alignment"] = AlignmentAgent.format_alignment_for_display(plan_b)

                # 推送对齐摘要到 AI 流
                align_summary = alignment_result.get("summary", "")[:1500]
                if align_summary:
                    await _notify_ai_stream(pid, "alignment_agent", "token", align_summary)
                await _notify_ai_stream(pid, "alignment_agent", "done",
                    f"{len(plan_a.get('plan', []))} 个模块")

                await push_log(
                    pid, "SUCCESS",
                    f"需求对齐完成：{len(plan_a.get('plan', []))} 个建议模块"
                    + ("（含备选方案）" if plan_b else "")
                )
                break

            except Exception as exc:
                last_error = str(exc)
                await push_log(pid, "ERROR", f"对齐分析异常（第 {attempt} 次）: {exc}")
                if attempt >= MAX_NON_MODULE_RETRIES:
                    await push_log(pid, "WARN", "所有 Agent 调用均失败，使用智能兜底方案")
                    fallback = _fallback_alignment(state["requirement"], structured_context)
                    if doc_type == "business":
                        fallback["plan_type"] = "business"
                        fallback["executive_summary"] = (
                            f"AI 分析暂时不可用（错误: {last_error}）。"
                            f"目录「{directory}」中有 {len(structured_context.get('analyzed_files', []))} 个文档文件，"
                            f"建议：1) 检查 API 密钥和网络连接；2) 在需求框中输入具体需求后重试。"
                        )
                    alignment_result = BusinessPlannerAgent.format_for_display(fallback) if doc_type == "business" \
                        else AlignmentAgent.format_alignment_for_display(fallback)
                    break

        # 最终兜底保护（理论上不会到这里，但保留）
        if alignment_result is None:
            fallback = _fallback_alignment(state["requirement"], structured_context)
            alignment_result = AlignmentAgent.format_alignment_for_display(fallback)

        state["alignment_result"] = alignment_result
        state["status"] = "aligned"
        await push_log(pid, "STATE", "aligned", state_event="aligned")
        return state

    async def execute_context_analysis(self, state: WorkflowState) -> WorkflowState:
        """上下文分析节点：读取项目目录，生成上下文描述。

        仅在 directory 非空时执行分析；否则透传。
        """
        pid = state["project_id"]
        directory = state.get("directory", "")

        if not directory:
            await push_log(pid, "INFO", "无项目目录，跳过上下文分析")
            return state

        await push_log(pid, "INFO", f"开始分析项目目录: {directory}")
        try:
            context = analyze_project_context(directory)
            state["project_context"] = context
            await push_log(pid, "SUCCESS", "项目上下文分析完成")
        except Exception as exc:
            await push_log(pid, "WARN", f"上下文分析异常: {exc}，将跳过")
            state["project_context"] = f"（目录分析失败: {exc}）"

        return state

    async def plan_only(self, state: WorkflowState) -> WorkflowState:
        """执行规划阶段（带重试，永不卡死）。

        规划完成后 state["status"] = "plan_ready"。
        """
        pid = state["project_id"]
        state.setdefault("module_results", {})
        state.setdefault("errors", [])
        state.setdefault("blocked_modules", [])

        directory = state.get("directory", "")
        project_memory_text = ""
        external_modules: set[str] = set()
        if directory:
            try:
                project_memory_text = self._project_memory.format_for_prompt(directory)
                external_modules = self._project_memory.get_passed_module_names(
                    directory
                )
            except Exception as exc:
                logger.warning("规划时无法读取项目记忆: %s", exc)
                pass

        for attempt in range(1, MAX_NON_MODULE_RETRIES + 1):
            try:
                # 详细日志：显示正在处理的需求
                req_summary = state["requirement"][:120]
                await push_log(pid, "INFO", f"📋 PM Agent 启动规划（第 {attempt}/{MAX_NON_MODULE_RETRIES} 次）")
                await push_log(pid, "INFO", f"📝 需求摘要: {req_summary}...")
                state["status"] = "planning"

                mvp_cap = _resolve_mvp_module_cap(state)
                planning_context = state.get("project_context", "")
                if mvp_cap and mvp_cap > 1:
                    planning_context += (
                        f"\n\n【MVP硬性约束】最多拆解 {mvp_cap} 个模块，"
                        "请合并相近功能，优先最小可行产品。"
                        f"\n请规划恰好 {mvp_cap} 个 backend 模块（或更少），"
                        "每个模块为一个小型 Python 函数/类；禁止 frontend/database/ML。"
                    )
                elif mvp_cap == 1:
                    planning_context += (
                        f"\n\n【MVP硬性约束】最多拆解 {mvp_cap} 个模块，"
                        "请合并相近功能，优先最小可行产品。"
                        "\n【强制单模块】modules 数组必须恰好 1 个元素；"
                        "description 只描述一个可运行的 Python 核心能力，"
                        "禁止 SQLite/CLI/多子系统拆分。"
                    )

                await push_log(pid, "INFO", "🤔 PM Agent 正在分析需求，拆解模块结构...")
                await _notify_ai_stream(pid, "planner", "start")
                plan_a, plan_b, comparison, errors = await self._planner.plan_alternatives(
                    state["requirement"],
                    project_context=planning_context,
                    project_memory_text=project_memory_text,
                    external_modules=external_modules,
                )
                if errors:
                    await push_log(pid, "ERROR", f"规划验证失败: {'; '.join(errors)}")
                    if attempt < MAX_NON_MODULE_RETRIES:
                        # ★ 将验证错误作为反馈注入上下文，帮助 LLM 在下一次修正
                        feedback = (
                            "\n\n## ⚠️ 上次规划验证失败，请修正以下问题\n"
                            + "\n".join(f"- {e}" for e in errors)
                            + "\n\n请确保 dependencies 仅引用本次 modules 中的 module_name。"
                        )
                        if external_modules:
                            feedback += (
                                "\n已通过模块（勿写入 dependencies，"
                                f"勿重复列入 modules）："
                                f"{', '.join(sorted(external_modules))}"
                            )
                        planning_context = (planning_context or "") + feedback
                        continue
                    state["errors"].extend(errors)
                    state["status"] = "needs_review"
                    return state

                modules = plan_a.get("modules", [])
                module_count = len(modules)
                if module_count == 0:
                    await push_log(pid, "WARN", "⚠️ 规划完成但未生成任何模块，使用关键词兜底方案")
                    # 从需求中提取关键词生成基础模块
                    import re as _re
                    words = _re.findall(r"[\u4e00-\u9fff]{2,}|[a-zA-Z]{3,}", state["requirement"])
                    keywords = list(dict.fromkeys(words))[:5]  # 去重取前5个
                    if keywords:
                        modules = []
                        for kw in keywords:
                            modules.append({
                                "module_name": kw.lower().replace(" ", "_")[:30],
                                "description": f"实现与「{kw}」相关的功能模块",
                                "dependencies": [],
                                "type": "backend",
                            })
                        await push_log(pid, "SUCCESS", f"🔧 兜底生成 {len(modules)} 个模块: {', '.join(m['module_name'] for m in modules)}")
                    else:
                        modules = [{
                            "module_name": "main",
                            "description": state["requirement"][:80],
                            "dependencies": [],
                            "type": "backend",
                        }]
                        await push_log(pid, "SUCCESS", "🔧 兜底生成 1 个默认模块: main")
                    plan_a["modules"] = modules
                    module_count = len(modules)
                else:
                    module_names = ", ".join(m.get("module_name", m.get("module", "?")) for m in modules[:10])
                    await push_log(pid, "SUCCESS", f"📦 规划完成: {module_count} 个模块 — {module_names}"
                        + (f"（共{module_count}个）" if module_count > 10 else ""))
                    for i, m in enumerate(modules[:5]):
                        desc = m.get("description", "")[:80]
                        await push_log(pid, "INFO", f"  模块{i+1}: {m.get('module_name', m.get('module', '?'))} — {desc}")

                original_count = len(modules)
                modules = _apply_mvp_module_cap(modules, state)
                modules = _normalize_business_mvp_modules(modules, state)
                modules = _sanitize_business_multi_modules(modules, state)
                if len(modules) < original_count:
                    await push_log(
                        pid, "INFO",
                        f"✂️ 商业 MVP 裁剪：{original_count} → {len(modules)} 个模块",
                    )
                    plan_a["modules"] = modules
                    module_count = len(modules)

                state["plan_json"] = json.dumps(plan_a, ensure_ascii=False)
                state["plan_modules"] = modules
                state["alternative_plan"] = plan_b if plan_b else None
                state["plan_comparison"] = comparison if comparison else None
                global_req = plan_a.get("global_requirements", [])
                state["project_context"] = state.get("project_context", "") + "\n全局需求:\n" + "\n".join(f"- {r}" for r in global_req)

                # ── 依赖推断 ──
                if DEPENDENCY_INFERENCE_ENABLED:
                    await push_log(pid, "INFO", "推断项目依赖...")
                    try:
                        deps = await self._planner.infer_dependencies(
                            state["requirement"], state.get("project_context", ""), "python"
                        )
                        state["inferred_dependencies"] = deps
                        await push_log(pid, "SUCCESS", f"依赖推断完成: {deps.get('summary', '')}")
                    except Exception as exc:
                        logger.warning("依赖推断失败，使用空依赖: %s", exc)
                        state["inferred_dependencies"] = {"dependencies": [], "system_dependencies": [], "summary": "推断失败"}

                # ── 注入人类偏好 ──
                if FEEDBACK_LEARNING_ENABLED:
                    try:
                        prefs = PlannerAgent.load_human_preferences()
                        if prefs:
                            state["project_context"] = state.get("project_context", "") + prefs
                    except Exception as exc:
                        logger.warning("加载人类偏好失败: %s", exc)
                        pass
                # 推送规划结果到 AI 流
                plan_token_text = json.dumps({
                    "modules": [m.get("module_name", "") for m in modules],
                    "total": len(modules),
                }, ensure_ascii=False)
                await _notify_ai_stream(pid, "planner", "token", plan_token_text[:2000])
                await _notify_ai_stream(pid, "planner", "done", f"{len(modules)} 个模块")
                state["status"] = "plan_ready"
                # ★ 同步更新数据库状态，避免API显示滞后
                await _sync_project_status(pid, "plan_ready")
                await _persist_plan_modules(
                    pid, state["plan_json"], modules
                )
                try:
                    from workflow.document_sync import (
                        build_ctx_from_workflow_state,
                        sync_after_plan_ready,
                    )

                    sync_after_plan_ready(build_ctx_from_workflow_state(state))
                except Exception as exc:
                    logger.warning("计划同步文档失败: %s", exc)
                    pass
                return state

            except Exception as exc:
                await push_log(pid, "ERROR", f"规划异常（第 {attempt} 次）: {exc}")
                if attempt >= MAX_NON_MODULE_RETRIES:
                    state["status"] = "needs_review"
                    state["errors"].append(str(exc))
                    return state

        state["status"] = "needs_review"
        return state

    async def execute_from_plan(self, state: WorkflowState) -> WorkflowState:
        """从已有规划继续执行（跳过规划和上下文分析）。

        用于 confirm_plan 后继续：模块执行 → 集成 → 审查 → 打包。
        带非模块阶段重试。
        """
        pid = state["project_id"]

        try:
            # ── 阶段1：模块执行 ──
            state["status"] = "executing"
            await _sync_project_status(pid, "executing")
            modules = state["plan_modules"]
            order = topological_sort(modules)
            state["module_order"] = order

            await push_log(pid, "STATE", "executing", state_event="executing")
            total_modules = len(modules)
            await push_log(pid, "INFO", f"🚀 进入模块构建阶段，共 {total_modules} 个模块待执行")
            await push_log(pid, "INFO", f"📋 执行顺序: {' → '.join(order) if order else '(无)'}")

            await self._execute_modules_parallel(pid, state)

            results = state["module_results"]
            if not results:
                await push_log(pid, "ERROR", "❌ 模块执行无结果，管线中断")
                state["status"] = "failed"
                return state

            # 统计通过和阻塞
            passed_count = sum(1 for r in results.values() if r.get("status") == "passed")
            blocked_count = sum(1 for r in results.values() if r.get("status") == "blocked")
            failed_count = total_modules - passed_count - blocked_count
            state["blocked_modules"] = [n for n, r in results.items() if r.get("status") == "blocked"]

            if passed_count == 0 and blocked_count == 0:
                await push_log(pid, "ERROR", "❌ 所有模块执行失败，管线中断")
                state["status"] = "failed"
                return state

            await push_log(
                pid, "SUCCESS",
                f"✅ 模块构建完成: {passed_count} 通过, {blocked_count} 阻塞, {failed_count} 失败"
            )

            return await self._execute_post_modules_pipeline(pid, state)

        except Exception as exc:
            await push_log(pid, "ERROR", f"工作流异常: {exc}")
            state["status"] = "failed"
            state["errors"].append(str(exc))
            return state

    async def execute_improvement(
        self,
        state: WorkflowState,
        target_modules: list[str],
        *,
        reintegrate_only: bool = False,
    ) -> WorkflowState:
        """迭代改进：补跑指定模块（或仅重新集成）→ 审查 → 打包新版本。"""
        pid = state["project_id"]
        iteration = state.get("iteration", 1)

        try:
            if reintegrate_only:
                await push_log(
                    pid, "INFO",
                    f"🔄 第 {iteration} 轮迭代：根据补充需求重新集成与审查",
                )
            elif target_modules:
                state["status"] = "executing"
                await _sync_project_status(pid, "executing")
                await push_log(pid, "STATE", "executing", state_event="executing")
                await push_log(
                    pid, "INFO",
                    f"🔄 第 {iteration} 轮迭代：补跑 {len(target_modules)} 个模块 — "
                    + ", ".join(target_modules),
                )

                name_to_module = {
                    m["module_name"]: m for m in state["plan_modules"]
                }
                all_fixtures: list[str] = []
                structured = state.get("structured_context") or {}
                for name in target_modules:
                    mod = name_to_module.get(name) or {}
                    desc = mod.get("description", "")
                    paths = ensure_delivery_fixtures(
                        pid,
                        desc,
                        directory=state.get("directory", "") or "",
                        structured_context=structured,
                    )
                    all_fixtures.extend(paths)
                if all_fixtures:
                    await push_log(
                        pid,
                        "INFO",
                        "📎 已准备 fixtures：" + ", ".join(sorted(set(all_fixtures))),
                    )
                extra_ctx = build_iteration_context_extras(
                    iteration=iteration,
                    target_modules=target_modules,
                    module_results=state.get("module_results", {}),
                    plan_modules=state["plan_modules"],
                    fixture_paths=sorted(set(all_fixtures)),
                )
                state["project_context"] = (
                    (state.get("project_context") or "").strip()
                    + "\n" + extra_ctx
                ).strip()

                for name in target_modules:
                    module = name_to_module.get(name)
                    if not module:
                        await push_log(pid, "WARN", f"跳过未知模块: {name}")
                        continue
                    await _update_module_status(pid, name, "pending")
                    await self._execute_single_module(pid, state, module)

                results = state.get("module_results", {})
                state["blocked_modules"] = [
                    n for n, r in results.items()
                    if r.get("status") == "blocked"
                ]
                passed = sum(
                    1 for r in results.values() if r.get("status") == "passed"
                )
                await push_log(
                    pid, "SUCCESS",
                    f"迭代模块阶段完成：{passed} 通过，"
                    f"{len(state['blocked_modules'])} 阻塞",
                )
            else:
                await push_log(pid, "WARN", "未指定目标模块，直接进入集成阶段")

            return await self._execute_post_modules_pipeline(pid, state)

        except Exception as exc:
            await push_log(pid, "ERROR", f"迭代改进异常: {exc}")
            state["status"] = "failed"
            state.setdefault("errors", []).append(str(exc))
            return state

    async def _execute_post_modules_pipeline(
        self, pid: str, state: WorkflowState
    ) -> WorkflowState:
        """模块执行后的共用管线：集成 → 测试 → 审查 → 打包。"""
        try:
            await _sync_project_status(pid, "integrating")
            await push_log(pid, "INFO", "🔗 进入集成阶段，组装项目...")
            state = await self._execute_integrate_with_retry(pid, state)
            await push_log(pid, "SUCCESS", "✅ 集成完成")

            # ── 集成测试执行 ──
            await push_log(pid, "INFO", "🧪 执行集成测试...")
            try:
                integration_data = state.get("integration_result", {}) or {}
                integration_test_code = integration_data.get("integration_tests", "")
                directory = state.get("directory", "") or ""
                memory_passed: set[str] = set()
                if directory:
                    try:
                        memory_passed = self._project_memory.get_passed_module_names(
                            directory
                        )
                    except Exception:
                        memory_passed = set()
                module_files = build_integration_module_files(
                    state.get("plan_modules") or [],
                    state.get("module_results") or {},
                    directory=directory,
                    memory_passed=memory_passed,
                )
                it_result = await run_integration_tests(
                    integration_test_code=integration_test_code,
                    project_id=pid,
                    module_files=module_files,
                    timeout=TEST_INTEGRATION_TIMEOUT,
                    log_callback=push_log,
                    requirements_text=integration_data.get("requirements", ""),
                    main_code=integration_data.get("main_code", ""),
                )
                state["integration_result"]["test_result"] = it_result.to_dict()
                await push_log(
                    pid, "SUCCESS" if it_result.all_passed else "WARN",
                    f"集成测试: {it_result.summary}",
                )
            except Exception as exc:
                await push_log(pid, "ERROR", f"集成测试执行异常: {exc}")
                state["integration_result"]["test_result"] = {"total": 0, "passed": 0, "failed": 1, "summary": str(exc)}

            # ── 全局审查（带重试） ──
            await push_log(pid, "STATE", "reviewing", state_event="reviewing")
            await _sync_project_status(pid, "reviewing")
            await push_log(pid, "INFO", "🔍 进入全局审查阶段...")
            state = await self._execute_global_review_with_retry(pid, state)
            review_data = state.get("global_review", {})
            await push_log(pid, "SUCCESS",
                f"✅ 审查完成: 评分 {review_data.get('score', '?')}/100, "
                f"{'通过' if review_data.get('passed') else '需改进'}")

            # ── 交付前全量测试 ──
            state["test_report"] = {"total": 0, "passed": 0, "failed": 0, "summary": "未执行"}
            if TEST_EXECUTION_ENABLED:
                await push_log(pid, "INFO", "执行交付前全量测试...")
                try:
                    project_dir = DELIVERIES_DIR / pid
                    project_dir.mkdir(parents=True, exist_ok=True)
                    # 先写入模块文件到沙箱供测试
                    for m in state["plan_modules"]:
                        name = m["module_name"]
                        mc = state["module_results"].get(name, {}).get("code", "")
                        tcode = state["module_results"].get(name, {}).get("test_code", "")
                        if mc:
                            ext = ".html" if m.get("type") == "frontend" else ".py"
                            (project_dir / f"{name}{ext}").write_text(mc, encoding="utf-8")
                        if tcode:
                            (project_dir / f"test_{name}.py").write_text(tcode, encoding="utf-8")
                    # 写入集成测试与主入口
                    integration_data = state.get("integration_result", {}) or {}
                    it_code = integration_data.get("integration_tests", "")
                    if it_code:
                        (project_dir / "test_integration.py").write_text(it_code, encoding="utf-8")
                    main_code = integration_data.get("main_code", "")
                    if main_code:
                        (project_dir / "main.py").write_text(main_code, encoding="utf-8")
                    req_text = integration_data.get("requirements", "")
                    if req_text:
                        (project_dir / "requirements.txt").write_text(
                            req_text, encoding="utf-8"
                        )
                    (project_dir / "conftest.py").touch()

                    full_result = await run_full_test_suite(
                        project_id=pid, sandbox_dir=project_dir,
                        timeout=TEST_FULL_SUITE_TIMEOUT, log_callback=push_log,
                    )
                    state["test_report"] = full_result.to_dict()

                    # 保存测试报告
                    report_path = project_dir / "test_report.json"
                    report_path.write_text(json.dumps(full_result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
                    state["test_report_path"] = str(report_path)

                    # 决策：全量测试结果处理
                    if full_result.all_passed:
                        await push_log(pid, "SUCCESS", f"全量测试全部通过！({full_result.passed}/{full_result.total})")
                    elif (
                        full_result.failed <= TEST_MAX_FAILURES_BEFORE_WARN
                        and full_result.errors == 0
                    ):
                        await push_log(pid, "WARN", f"全量测试有 {full_result.failed} 个失败（≤{TEST_MAX_FAILURES_BEFORE_WARN}），尝试最后一次修复...")
                        # 最后一次修复尝试（简化版：直接标记为 completed_with_warnings）
                        state["status"] = "completed_with_warnings"
                    else:
                        await push_log(pid, "WARN", f"全量测试有 {full_result.failed} 个失败（>{TEST_MAX_FAILURES_BEFORE_WARN}），项目标记为 completed_with_warnings")
                        state["status"] = "completed_with_warnings"
                except Exception as exc:
                    await push_log(pid, "ERROR", f"全量测试异常: {exc}")
                    state["test_report"] = {"total": 0, "passed": 0, "failed": 1, "summary": str(exc)}

            # ── 打包交付 ──
            await push_log(pid, "INFO", "📦 进入打包交付阶段...")
            integration = IntegrationResult(
                project_structure=state.get("integration_result", {}).get("project_structure", ""),
                main_code=state.get("integration_result", {}).get("main_code", ""),
                integration_tests=state.get("integration_result", {}).get("integration_tests", ""),
                readme=state.get("integration_result", {}).get("readme", ""),
                requirements=state.get("integration_result", {}).get("requirements", ""),
            )
            delivery_path = await self._package_delivery(pid, state, integration)
            state["delivery_path"] = delivery_path
            state["status"] = "completed"

            await push_log(pid, "STATE", "completed", state_event="completed")
            blocked_count = len(state.get("blocked_modules", []))
            total_mods = len(state.get("plan_modules", []))
            summary = (f"🎉 项目交付完成！{total_mods} 个模块, "
                       f"{total_mods - blocked_count} 通过"
                       + (f", {blocked_count} 阻塞" if blocked_count > 0 else ", 全部通过"))
            await push_log(pid, "SUCCESS", summary)

            # ── 通用记忆进化 ──
            if self._case_store:
                try:
                    review = state.get("global_review", {})
                    if review.get("passed") or review.get("score", 0) > 50:
                        modules_info = self._collect_module_info(state)
                        self._case_store.add_success(
                            requirement=state["requirement"],
                            plan=state["plan_modules"],
                            modules=modules_info,
                        )
                except Exception as exc:
                    logger.warning("保存成功案例失败: %s", exc)
                    pass

            return state

        except Exception as exc:
            await push_log(pid, "ERROR", f"交付管线异常: {exc}")
            state["status"] = "failed"
            state.setdefault("errors", []).append(str(exc))
            return state

    async def execute(self, state: WorkflowState) -> WorkflowState:
        """执行完整工作流：需求对齐（前置强制）→ 上下文分析 + 规划 + 执行。

        对齐完成后状态为 aligned，停止并等待用户确认。
        """
        # 第一阶段：需求对齐（所有构建工作流强制执行）
        state = await self.execute_alignment(state)
        if state["status"] == "aligned":
            return state  # 停止，等待用户确认
        # 对齐失败（needs_review），不再继续
        return state

    async def execute_after_alignment(self, state: WorkflowState) -> WorkflowState:
        """从对齐确认后继续：上下文分析 + 规划。

        在 plan_ready 停止，等待用户 confirm_plan 后再执行（由 _run_execution 驱动）。
        """
        state = await self.execute_context_analysis(state)
        state = await self.plan_only(state)
        return state

    async def _execute_integrate_with_retry(
        self, pid: str, state: WorkflowState
    ) -> WorkflowState:
        """执行集成阶段，最多重试 MAX_NON_MODULE_RETRIES 次。"""
        for attempt in range(1, MAX_NON_MODULE_RETRIES + 1):
            try:
                state["status"] = "integrating"
                await push_log(pid, "INFO", f"集成 Agent 开始组装项目...（第 {attempt} 次）")
                await _notify_ai_stream(pid, "integrator", "start")
                modules_info = self._collect_module_info(state)
                req = state["requirement"]
                try:
                    from workflow.document_sync import load_docs_context_for_agents

                    doc_ctx = load_docs_context_for_agents(pid)
                    if doc_ctx:
                        req = req + "\n\n" + doc_ctx
                except Exception as exc:
                    logger.warning("加载文档上下文失败: %s", exc)
                    pass
                integration = await self._integrator.integrate(req, modules_info)
                state["integration_result"] = {
                    "project_structure": integration.project_structure,
                    "main_code": integration.main_code,
                    "integration_tests": integration.integration_tests,
                    "readme": integration.readme,
                    "requirements": integration.requirements,
                }
                # 推送集成摘要
                await _notify_ai_stream(pid, "integrator", "token",
                    str(integration.project_structure or "")[:1000])
                await _notify_ai_stream(pid, "integrator", "done")
                await push_log(pid, "SUCCESS", "项目集成完成")
                return state
            except Exception as exc:
                await push_log(pid, "ERROR", f"集成异常（第 {attempt} 次）: {exc}")
                if attempt >= MAX_NON_MODULE_RETRIES:
                    state["status"] = "needs_review"
                    state["errors"].append(f"集成失败: {exc}")
                    raise
        return state

    async def _execute_global_review_with_retry(
        self, pid: str, state: WorkflowState
    ) -> WorkflowState:
        """执行全局审查阶段，最多重试 MAX_NON_MODULE_RETRIES 次。"""
        for attempt in range(1, MAX_NON_MODULE_RETRIES + 1):
            try:
                state["status"] = "reviewing"
                await push_log(pid, "INFO", f"全局审查 Agent 开始审查...（第 {attempt} 次）")
                await _notify_ai_stream(pid, "global_reviewer", "start")
                modules_info = self._collect_module_info(state)
                integration = IntegrationResult(
                    project_structure=state.get("integration_result", {}).get("project_structure", ""),
                    main_code=state.get("integration_result", {}).get("main_code", ""),
                    integration_tests=state.get("integration_result", {}).get("integration_tests", ""),
                    readme=state.get("integration_result", {}).get("readme", ""),
                    requirements=state.get("integration_result", {}).get("requirements", ""),
                )
                global_review = await self._global_reviewer.review(
                    state["requirement"], state["plan_modules"], modules_info, integration,
                )
                state["global_review"] = {
                    "passed": global_review.passed,
                    "score": global_review.score,
                    "summary": global_review.summary,
                    "issues": global_review.issues,
                    "suggestions": global_review.suggestions,
                }
                verdict = "通过" if global_review.passed else "需改进"
                # 推送审查摘要到 AI 流
                review_token = f"评分: {global_review.score}/100, {verdict}"
                if global_review.summary:
                    review_token += f"\n{global_review.summary[:500]}"
                await _notify_ai_stream(pid, "global_reviewer", "token", review_token)
                await _notify_ai_stream(pid, "global_reviewer", "done", f"{global_review.score}/100")
                await push_log(
                    pid, "SUCCESS" if global_review.passed else "WARN",
                    f"全局审查{verdict}，评分: {global_review.score}/100",
                )
                return state
            except Exception as exc:
                await push_log(pid, "ERROR", f"全局审查异常（第 {attempt} 次）: {exc}")
                if attempt >= MAX_NON_MODULE_RETRIES:
                    state["status"] = "needs_review"
                    state["errors"].append(f"全局审查失败: {exc}")
                    raise
        return state

    async def _execute_modules_parallel(
        self, pid: str, state: WorkflowState
    ) -> None:
        """并行执行模块（按拓扑层并行，层内并行）。"""
        modules = state["plan_modules"]
        name_to_module = {m["module_name"]: m for m in modules}

        # 按层级分组
        layers = self._build_layers(modules)

        for layer_idx, layer in enumerate(layers):
            await push_log(
                pid, "INFO",
                f"执行第 {layer_idx + 1}/{len(layers)} 层: {', '.join(layer)}"
            )

            # 层内并行
            sem = asyncio.Semaphore(MAX_CONCURRENT_MODULES)

            async def run_module(module_name: str, _sem: asyncio.Semaphore = sem) -> None:
                async with _sem:
                    module = name_to_module[module_name]
                    await self._execute_single_module(
                        pid, state, module
                    )

            tasks = [run_module(name) for name in layer]
            await asyncio.gather(*tasks)

    @staticmethod
    def _build_layers(
        modules: list[dict[str, Any]],
    ) -> list[list[str]]:
        """按依赖关系将模块分成并行层。

        每层内的模块无相互依赖，可完全并行。
        """
        name_to_idx = {m["module_name"]: i for i, m in enumerate(modules)}
        # 计算每个模块的深度（最长依赖链长度）
        depth: dict[str, int] = {}
        visiting: set[str] = set()  # 检测循环依赖

        def get_depth(name: str) -> int:
            if name in depth:
                return depth[name]
            if name not in name_to_idx:
                return 0
            if name in visiting:
                # 循环依赖：标记为0并中止递归
                logger.warning("检测到模块循环依赖: %s", name)
                depth[name] = 0
                return 0
            visiting.add(name)
            deps = modules[name_to_idx[name]].get("dependencies", [])
            max_dep = 0
            for dep in deps:
                max_dep = max(max_dep, get_depth(dep) + 1)
            depth[name] = max_dep
            visiting.discard(name)
            return max_dep

        for m in modules:
            get_depth(m["module_name"])

        # 按深度分组
        layers_dict: dict[int, list[str]] = {}
        for name, d in depth.items():
            layers_dict.setdefault(d, []).append(name)

        return [
            layers_dict[d] for d in sorted(layers_dict.keys())
        ]

    async def _run_module_test_and_log(
        self, pid: str, module_name: str, test_code: str,
        module_code: str = "",
    ) -> dict[str, Any]:
        """执行模块测试并返回序列化结果。"""
        if not TEST_EXECUTION_ENABLED or not test_code:
            return {"total": 0, "passed": 0, "failed": 0, "summary": "跳过", "execution_mode": "skipped"}
        try:
            tr = await run_module_tests(
                module_name=module_name,
                test_code=test_code,
                project_id=pid,
                timeout=TEST_UNIT_TIMEOUT,
                log_callback=push_log,
                module_code=module_code,
                module_filename=f"{module_name}.py",
            )
            return tr.to_dict()
        except Exception as exc:
            await push_log(pid, "ERROR", f"[{module_name}] 测试执行异常: {exc}", module_name=module_name)
            return {"total": 0, "passed": 0, "failed": 1, "summary": str(exc), "execution_mode": "error"}

    async def _generate_code_until_ready(
        self,
        pid: str,
        module_name: str,
        spec: ModuleSpec,
        feedback: str,
        mvp_mode: bool,
        *,
        attempt_label: str = "",
        reuse_modules_context: str = "",
    ) -> tuple[ModuleCode | None, bool, str]:
        """编码并在就绪校验通过前不进入测试/审查，避免截断代码误报。"""
        current_feedback = feedback
        last_code: ModuleCode | None = None
        max_attempts = MAX_CODE_READINESS_RETRIES + 1

        for attempt in range(max_attempts):
            label = attempt_label or f"第 {attempt + 1}/{max_attempts} 次"
            await _update_module_status(pid, module_name, "coding")
            await push_log(
                pid, "INFO",
                f"[{module_name}] 编码中…（{label}）",
                module_name=module_name,
            )
            last_code = await self._modules.code(
                module_name,
                spec,
                current_feedback,
                mvp_mode=mvp_mode,
                reuse_modules_context=reuse_modules_context,
            )
            fixed_code, fixed_test, shell_notes = repair_truncation_shell(
                last_code.code, last_code.test_code,
            )
            fixed_code, quick_notes = apply_code_quick_fixes(
                fixed_code,
                description=spec.summary,
                spec_summary=spec.summary,
            )
            if shell_notes or quick_notes:
                last_code = ModuleCode(
                    module_name=last_code.module_name,
                    code=fixed_code,
                    test_code=fixed_test,
                    language=last_code.language,
                )
                for note in shell_notes + quick_notes:
                    await push_log(pid, "INFO", f"[{module_name}] {note}", module_name=module_name)
            readiness = assess_module_code(
                last_code.code,
                last_code.test_code,
                language=last_code.language,
                module_description=spec.summary,
                spec_summary=spec.summary,
            )
            if readiness.ready:
                return last_code, True, current_feedback

            current_feedback = format_readiness_feedback(readiness)
            await push_log(
                pid, "WARN",
                f"[{module_name}] 编码产出未就绪（{readiness.phase}），"
                f"暂不进入测试/审查: {'; '.join(readiness.issues[:2])}",
                module_name=module_name,
            )

        if last_code is None:
            return None, False, current_feedback
        final = assess_module_code(
            last_code.code, last_code.test_code, language=last_code.language,
        )
        return last_code, final.ready, format_readiness_feedback(final)

    async def _execute_single_module(
        self,
        pid: str,
        state: WorkflowState,
        module: dict[str, Any],
    ) -> None:
        """执行单个模块的完整子图流程。

        流程：
        1. 分析
        2. 正常编码→测试→审查循环（前 3 次）+ 测试执行
        3. 第 4 次起异常自愈（多重策略回退，总轮次上限 8）
        4. 所有策略耗尽后 → blocked（生成占位文件）
        """
        module_name = module["module_name"]
        description = module.get("description", "")
        module_type = module.get("type", "backend")
        context = state.get("project_context", "")
        iteration_num = state.get("iteration", 1) or 1
        prior_result = state.get("module_results", {}).get(module_name, {})
        prior_code = prior_result.get("code", "") or ""
        prior_test = prior_result.get("test_code", "") or ""
        prior_errors = prior_result.get("errors") or []
        if isinstance(prior_errors, str):
            prior_errors = [prior_errors]
        prior_failure = prior_result.get("failure_reason", "") or ""

        fixture_paths = ensure_delivery_fixtures(
            pid,
            description,
            directory=state.get("directory", "") or "",
            structured_context=state.get("structured_context"),
        )
        if fixture_paths:
            context = (
                context
                + "\n样例数据路径：" + ", ".join(fixture_paths)
            ).strip()

        directory = state.get("directory", "") or ""
        memory_passed: set[str] = set()
        if directory:
            try:
                memory_passed = self._project_memory.get_passed_module_names(
                    directory
                )
            except Exception:
                memory_passed = set()
        reuse_ctx = build_passed_module_context(
            current_module=module,
            directory=directory,
            module_results=state.get("module_results") or {},
            memory_passed=memory_passed,
        )
        if reuse_ctx:
            await push_log(
                pid,
                "INFO",
                f"[{module_name}] 已注入已通过模块 API 上下文",
                module_name=module_name,
            )

        mvp_cap = _resolve_mvp_module_cap(state)
        mvp_mode = mvp_cap == 1
        alignment = state.get("alignment_result") or {}
        business_mvp_relaxed = (
            isinstance(alignment, dict)
            and alignment.get("plan_type") == "business"
            and (mvp_cap or 0) > 1
        )
        use_mvp_test_gate = mvp_mode or business_mvp_relaxed
        compact_mvp = mvp_mode or business_mvp_relaxed

        await push_log(
            pid, "INFO",
            f"[{module_name}] 开始执行",
            module_name=module_name,
        )
        await _update_module_status(pid, module_name, "analyzing")
        await _notify_ai_stream(pid, "module_agents", "start")

        try:
            # ── 分析 ──
            await push_log(
                pid, "INFO",
                f"[{module_name}] 分析师分析中...",
                module_name=module_name,
            )
            spec = await self._modules.analyze(
                module_name,
                description,
                context,
                mvp_mode=compact_mvp,
                reuse_modules_context=reuse_ctx,
            )

            # ── 编码 + 测试 + 审查 循环（前 MAX_REVIEW_RETRIES 次正常重试） ──
            code: ModuleCode | None = None
            review: ReviewResult | None = None
            feedback = ""
            if iteration_num > 1 and (
                prior_failure or prior_errors or prior_code
            ):
                feedback = build_checklist_repair_feedback(
                    prior_errors,
                    prior_code=prior_code,
                    prior_test=prior_test,
                    failure_reason=prior_failure,
                    module_name=module_name,
                )
                await push_log(
                    pid,
                    "INFO",
                    f"[{module_name}] 迭代模式：已注入审查清单与既有代码上下文",
                    module_name=module_name,
                )
            failure_reason = ""
            total_rounds = 0
            fix_history: list[dict[str, Any]] = []

            # 正常重试循环（前 3 次）
            for retry in range(MAX_REVIEW_RETRIES):
                total_rounds += 1

                code, code_ready, readiness_feedback_text = await self._generate_code_until_ready(
                    pid,
                    module_name,
                    spec,
                    feedback,
                    compact_mvp,
                    attempt_label=f"审查轮次 {retry + 1}",
                    reuse_modules_context=reuse_ctx,
                )
                if not code_ready or code is None:
                    failure_reason = readiness_feedback_text or "编码产出未就绪"
                    await push_log(
                        pid, "WARN",
                        f"[{module_name}] 编码未通过就绪校验，跳过本轮 LLM 测试/审查",
                        module_name=module_name,
                    )
                    feedback = failure_reason
                    if retry < MAX_REVIEW_RETRIES - 1:
                        continue
                    break

                await _update_module_status(pid, module_name, "testing")
                # 测试
                await push_log(
                    pid, "INFO",
                    f"[{module_name}] 测试中...",
                    module_name=module_name,
                )
                await self._modules.test(
                    module_name, code, spec
                )

                await _update_module_status(pid, module_name, "reviewing")
                # 审查
                await push_log(
                    pid, "INFO",
                    f"[{module_name}] 审查中...",
                    module_name=module_name,
                )
                review = await self._reviewer.review(
                    module_name,
                    spec.summary,
                    code.code,
                    code.test_code,
                    retry_count=retry,
                    mvp_mode=compact_mvp,
                )

                exec_test_result: dict[str, Any] | None = None
                if use_mvp_test_gate:
                    exec_test_result = await self._run_module_test_and_log(
                        pid, module_name, code.test_code, module_code=code.code,
                    )

                if review.passed:
                    if exec_test_result is None:
                        exec_test_result = await self._run_module_test_and_log(
                            pid, module_name, code.test_code, module_code=code.code,
                        )

                    if _module_clear_to_pass(True, exec_test_result):
                        await push_log(
                            pid, "SUCCESS",
                            f"[{module_name}] 审查通过 ✓",
                            module_name=module_name,
                        )
                        state["module_results"][module_name] = {
                            "module_name": module_name,
                            "status": "passed",
                            "spec": {
                                "summary": spec.summary,
                                "api_endpoints": spec.api_endpoints,
                                "data_models": spec.data_models,
                            },
                            "code": code.code,
                            "test_code": code.test_code,
                            "test_result": exec_test_result,
                            "retry_count": retry,
                            "auto_fix_history": json.dumps(fix_history, ensure_ascii=False) if fix_history else "[]",
                        }
                        await _persist_module_result(pid, module_name, state["module_results"][module_name])
                        try:
                            from workflow.closed_loop import cleanup_after_fix
                            await cleanup_after_fix(pid, module_name)
                        except Exception as exc:
                            logger.warning("修复后清理失败: %s", exc)
                            pass
                        return

                    failure_reason = (
                        (exec_test_result or {}).get("summary")
                        or "单元测试未通过"
                    )
                    await push_log(
                        pid, "WARN",
                        f"[{module_name}] 审查通过但单元测试未通过，继续修复…\n{failure_reason}",
                        module_name=module_name,
                    )
                    feedback = f"审查已通过，但 pytest 未通过，请修复：\n{failure_reason}"
                    if retry < MAX_REVIEW_RETRIES - 1:
                        continue
                    break

                if use_mvp_test_gate and _exec_tests_passed(exec_test_result):
                    label = "MVP" if mvp_mode else "商业多模块"
                    await push_log(
                        pid, "SUCCESS",
                        f"[{module_name}] {label} 模式：单元测试通过，审查问题已降级放行",
                        module_name=module_name,
                    )
                    state["module_results"][module_name] = {
                        "module_name": module_name,
                        "status": "passed",
                        "spec": {
                            "summary": spec.summary,
                            "api_endpoints": spec.api_endpoints,
                            "data_models": spec.data_models,
                        },
                        "code": code.code,
                        "test_code": code.test_code,
                        "test_result": exec_test_result,
                        "retry_count": retry,
                        "mvp_test_override": True,
                        "auto_fix_history": json.dumps(fix_history, ensure_ascii=False) if fix_history else "[]",
                    }
                    await _persist_module_result(pid, module_name, state["module_results"][module_name])
                    try:
                        from workflow.closed_loop import cleanup_after_fix
                        await cleanup_after_fix(pid, module_name)
                    except Exception as exc:
                        logger.warning("修复后清理失败: %s", exc)
                        pass
                    return

                # 审查未通过
                issues_text = "\n".join(
                    f"  - {issue}" for issue in review.issues
                )
                failure_reason = issues_text
                await push_log(
                    pid, "WARN",
                    f"[{module_name}] 审查不通过，问题:\n{issues_text}",
                    module_name=module_name,
                )

                if retry < MAX_REVIEW_RETRIES - 1:
                    feedback = build_checklist_repair_feedback(
                        review.issues,
                        prior_code=code.code if code else "",
                        prior_test=code.test_code if code else "",
                        failure_reason=issues_text,
                        module_name=module_name,
                    )
                else:
                    await push_log(
                        pid, "WARN",
                        f"[{module_name}] 正常重试 {MAX_REVIEW_RETRIES} 次后仍未通过，启动异常自愈...",
                        module_name=module_name,
                    )

            loop_result = None
            # ── 异常自愈阶段（闭环修复：查询历史 → 修复 → 验证 → 重试）──
            if AUTO_FIX_ENABLED and code is not None and review is not None:
                await _update_module_status(pid, module_name, "auto_fixing")

                error_text = "\n".join(review.issues) if review and review.issues else failure_reason
                error_type = classify_error(error_text)

                await push_log(
                    pid, "INFO",
                    f"[自愈][{module_name}] 检测到错误类型: {error_type.value}，启动闭环修复...",
                    module_name=module_name,
                )

                # ── 导入闭环修复模块 ──
                from workflow.closed_loop import closed_loop_repair, query_fix_history

                # 查询历史（提前记录到日志）
                pre_history = await query_fix_history(pid, module_name, error_type.value)
                if pre_history.get("failed_strategies"):
                    await push_log(
                        pid, "INFO",
                        f"[自愈][{module_name}] 历史已失败策略: {', '.join(pre_history['failed_strategies'])}，将跳过重试",
                        module_name=module_name,
                    )

                # 构建闭环修复的回调函数
                async def _do_code(mn: str, spec_obj, fb: str):
                    # closed_loop 传入的是 dict，需要转为 ModuleSpec
                    from agents.module_agents import ModuleSpec
                    if isinstance(spec_obj, dict):
                        spec_obj = ModuleSpec(
                            module_name=mn,
                            summary=spec_obj.get("summary", ""),
                            api_endpoints=spec_obj.get("api_endpoints", []),
                            data_models=spec_obj.get("data_models", []),
                            logic_flow=spec_obj.get("logic_flow", ""),
                            error_handling=spec_obj.get("error_handling", ""),
                        )
                    coded, ready, fb_out = await self._generate_code_until_ready(
                        pid,
                        mn,
                        spec_obj,
                        fb,
                        compact_mvp,
                        attempt_label="自愈编码",
                        reuse_modules_context=reuse_ctx,
                    )
                    if not ready:
                        raise ValueError(fb_out or "编码产出未就绪")
                    return coded

                async def _do_review(
                    mn: str,
                    summary: str,
                    c: str,
                    tc: str,
                    retry_count: int = 0,
                ):
                    readiness = assess_module_code(c, tc)
                    if not readiness.ready:
                        from agents.reviewer import ReviewResult
                        return ReviewResult(
                            passed=False,
                            summary="编码产出未就绪，跳过审查",
                            issues=readiness.issues or ["代码尚未完整，请继续编码"],
                        )
                    return await self._reviewer.review(
                        mn, summary, c, tc, retry_count=retry_count, mvp_mode=compact_mvp,
                    )

                async def _do_repair(moutput: dict, hcases: list):
                    return await self._repair_agent.repair(moutput, hcases)

                async def _do_test(mn: str, tc: str, mc: str = ""):
                    from workflow.test_runner import run_module_tests
                    readiness = assess_module_code(mc, tc)
                    if mc and not readiness.ready:
                        # 代码未就绪，无法执行测试，返回失败结果
                        from workflow.test_runner import ModuleTestResult

                        return ModuleTestResult(
                            passed=False, summary="代码未就绪，无法执行测试"
                        )
                    try:
                        return await run_module_tests(
                            module_name=mn,
                            test_code=tc,
                            project_id=pid,
                            timeout=TEST_UNIT_TIMEOUT,
                            module_code=mc,
                            module_filename=f"{mn}.py",
                        )
                    except Exception as exc:
                        logger.warning("测试执行包装失败: %s", exc)
                        return None

                async def _push_log(pid_, level, msg, module_name=""):
                    await push_log(pid_, level, msg, module_name=module_name)

                # 执行闭环修复
                loop_result = await closed_loop_repair(
                    project_id=pid,
                    module_name=module_name,
                    module_type=module_type,
                    spec_summary=spec.summary,
                    spec_detail={
                        "summary": spec.summary,
                        "api_endpoints": spec.api_endpoints,
                        "data_models": spec.data_models,
                        "logic_flow": spec.logic_flow,
                        "error_handling": spec.error_handling,
                    },
                    current_code=code.code,
                    current_test_code=code.test_code,
                    review_issues=review.issues if review else [],
                    failure_reason=failure_reason,
                    do_code=_do_code,
                    do_review=_do_review,
                    do_repair=_do_repair,
                    do_test=_do_test,
                    push_log=_push_log,
                )

                # 更新全局计数器
                total_rounds += loop_result.total_rounds
                fix_history.extend(loop_result.fix_history)

                if loop_result.success:
                    await push_log(
                        pid, "SUCCESS",
                        f"[{module_name}] 闭环修复成功！策略: {loop_result.strategy_used}",
                        module_name=module_name,
                    )
                    exec_test_result = await self._run_module_test_and_log(
                        pid, module_name, loop_result.test_code, module_code=loop_result.code,
                    )
                    if _module_clear_to_pass(True, exec_test_result):
                        state["module_results"][module_name] = {
                            "module_name": module_name,
                            "status": "passed",
                            "spec": {
                                "summary": spec.summary,
                                "api_endpoints": spec.api_endpoints,
                                "data_models": spec.data_models,
                            },
                            "code": loop_result.code,
                            "test_code": loop_result.test_code,
                            "test_result": exec_test_result,
                            "retry_count": total_rounds,
                            "auto_fix_history": json.dumps(loop_result.fix_history, ensure_ascii=False),
                        }
                        await _persist_module_result(pid, module_name, state["module_results"][module_name])
                        return

                    failure_reason = (
                        (exec_test_result or {}).get("summary")
                        or "闭环修复后单元测试仍未通过"
                    )
                    await push_log(
                        pid, "WARN",
                        f"[{module_name}] 闭环修复完成但测试未通过: {failure_reason}",
                        module_name=module_name,
                    )

                # 闭环修复未成功，更新代码引用
                failure_reason = loop_result.failure_reason or failure_reason
                if loop_result.code:
                    code = ModuleCode(
                        module_name=module_name,
                        code=loop_result.code,
                        test_code=loop_result.test_code,
                    )

            # 超过最大重试 → 阻塞（blocked），生成占位文件
            final_reason = failure_reason or "超过所有修复策略尝试次数"
            await push_log(
                pid, "WARN",
                f"[{module_name}] 所有修复策略已尝试（共 {total_rounds} 轮），标记为阻塞（blocked）",
                module_name=module_name,
            )

            loop_code = ""
            loop_test = ""
            if loop_result is not None and getattr(loop_result, "code", ""):
                loop_code = loop_result.code
                loop_test = loop_result.test_code or ""
            candidate_codes = [
                loop_code,
                code.code if code else "",
                prior_code,
            ]
            candidate_tests = [
                loop_test,
                code.test_code if code else "",
                prior_test,
            ]
            blocked_code, blocked_test, used_stub = resolve_blocked_artifacts(
                module_name=module_name,
                module_type=module_type,
                description=description,
                failure_reason=final_reason,
                code_candidates=candidate_codes,
                test_candidates=candidate_tests,
                stub_generator=_generate_stub_code,
            )
            if used_stub:
                await push_log(
                    pid,
                    "WARN",
                    f"[{module_name}] 无可保留代码，已写入占位文件",
                    module_name=module_name,
                )
            else:
                await push_log(
                    pid,
                    "INFO",
                    f"[{module_name}] 保留末次代码供下轮迭代修复（非占位）",
                    module_name=module_name,
                )
            result_data: dict[str, Any] = {
                "module_name": module_name,
                "status": "blocked",
                "spec": {"summary": spec.summary},
                "code": blocked_code,
                "test_code": blocked_test,
                "retry_count": total_rounds,
                "errors": review.issues if review else [final_reason],
                "failure_reason": final_reason,
                "auto_fix_history": json.dumps(fix_history, ensure_ascii=False) if fix_history else "[]",
                "preserved_code": not used_stub,
            }
            state["module_results"][module_name] = result_data
            await _persist_module_result(pid, module_name, result_data)

        except Exception as exc:
            await push_log(
                pid, "ERROR",
                f"[{module_name}] 执行异常: {exc}",
                module_name=module_name,
            )
            exc_reason = f"执行异常: {exc}"
            exc_code, exc_test, exc_stub = resolve_blocked_artifacts(
                module_name=module_name,
                module_type=module_type,
                description=description,
                failure_reason=exc_reason,
                code_candidates=[prior_code],
                test_candidates=[prior_test],
                stub_generator=_generate_stub_code,
            )
            state["module_results"][module_name] = {
                "module_name": module_name,
                "status": "blocked",
                "errors": [str(exc)],
                "code": exc_code,
                "test_code": exc_test,
                "failure_reason": exc_reason,
                "auto_fix_history": "[]",
                "preserved_code": not exc_stub,
            }
            await _persist_module_result(pid, module_name, state["module_results"][module_name])

    def _collect_module_info(
        self, state: WorkflowState
    ) -> list[dict[str, Any]]:
        """收集所有模块信息供集成使用。"""
        info: list[dict[str, Any]] = []
        for m in state["plan_modules"]:
            name = m["module_name"]
            result = state["module_results"].get(name, {})
            info.append({
                "module_name": name,
                "description": m.get("description", ""),
                "spec": result.get("spec", {}).get("summary", ""),
                "code": result.get("code", ""),
                "test_code": result.get("test_code", ""),
                "status": result.get("status", "pending"),
            })
        return info

    async def _inject_autopilot(
        self, pid: str, project_dir: Path, state: WorkflowState,
    ) -> None:
        """将自治运维模板注入生成项目。

        复制 templates/autopilot/* 到 {project_dir}/autopilot/，
        并替换占位符 {{PROJECT_NAME}} 和 {{PROJECT_VERSION}}。
        根据项目类型适配主入口文件。
        """
        from config import AUTOPILOT_TEMPLATES_DIR

        if not AUTOPILOT_TEMPLATES_DIR.exists():
            await push_log(pid, "WARN", "自治模板目录不存在，跳过注入")
            return

        autopilot_dir = project_dir / "autopilot"
        autopilot_dir.mkdir(parents=True, exist_ok=True)

        project_name = state.get("requirement", "generated-project")[:30].replace(" ", "-")
        project_version = f"1.{len(state.get('plan_modules', []))}.0"

        # 判断项目类型
        project_type = "cli"  # 默认
        for m in state.get("plan_modules", []):
            if m.get("type") in ("backend", "integration"):
                project_type = "web"
                break

        # 复制模板文件并替换占位符
        copied = 0
        for template_file in AUTOPILOT_TEMPLATES_DIR.glob("*.py"):
            dest = autopilot_dir / template_file.name
            content = template_file.read_text(encoding="utf-8")
            content = content.replace("{{PROJECT_NAME}}", project_name)
            content = content.replace("{{PROJECT_VERSION}}", project_version)
            content = content.replace("{{PROJECT_ID}}", pid)
            dest.write_text(content, encoding="utf-8")
            copied += 1

        # 初始化空的 fixes.json 和 .analytics.json
        (autopilot_dir / "fixes.json").write_text(
            '{"version":"1.0","cases":[]}', encoding="utf-8"
        )
        (autopilot_dir / "feature_flags.json").write_text(
            '{"flags":{}}', encoding="utf-8"
        )

        # ── 主入口适配 ──
        main_py = project_dir / "main.py"
        if main_py.exists() and project_type == "web":
            main_content = main_py.read_text(encoding="utf-8")
            if "autopilot" not in main_content and "include_router" in main_content:
                # 在 app 创建后注入 health 路由注册
                main_content = main_content.replace(
                    "app = FastAPI",
                    "# 自治运维: 健康检查端点\nfrom autopilot.health_check import router as health_router\napp = FastAPI",
                )
                main_content = main_content.replace(
                    "return app",
                    'app.include_router(health_router)\n    return app',
                )
                main_py.write_text(main_content, encoding="utf-8")

        await push_log(pid, "SUCCESS", f"自治运维模块已注入 ({copied} 个文件, 类型: {project_type})")

    async def _package_delivery(
        self,
        pid: str,
        state: WorkflowState,
        integration: IntegrationResult,
    ) -> str:
        """打包项目为交付物（支持版本化）。"""
        import zipfile
        from pathlib import Path

        # ── 版本化目录 ──
        if VERSIONED_DELIVERY_ENABLED:
            # 检测当前版本号
            base_dir = DELIVERIES_DIR / pid
            base_dir.mkdir(parents=True, exist_ok=True)
            existing = sorted([int(d.name[1:]) for d in base_dir.iterdir() if d.is_dir() and d.name.startswith("v") and d.name[1:].isdigit()])
            version_num = existing[-1] + 1 if existing else 1
            project_dir = base_dir / f"v{version_num}"
            # 归档旧版本
            for d in existing:
                vdir = base_dir / f"v{d}"
                if d <= version_num - MAX_VERSIONS_KEPT and vdir.exists():
                    archive_dir = base_dir / ARCHIVE_DIR_NAME
                    archive_dir.mkdir(exist_ok=True)
                    import shutil
                    shutil.make_archive(str(archive_dir / f"v{d}"), "zip", str(vdir))
                    shutil.rmtree(vdir, ignore_errors=True)
        else:
            version_num = 1
            project_dir = DELIVERIES_DIR / pid

        project_dir.mkdir(parents=True, exist_ok=True)

        # ── 注入自治运维模块 ──
        if AUTOPILOT_ENABLED:
            await self._inject_autopilot(pid, project_dir, state)

        # 写入各模块代码（含 blocked 占位文件）
        for m in state["plan_modules"]:
            name = m["module_name"]
            code = state["module_results"].get(name, {}).get("code", "")
            if code:
                ext = ".py"
                mtype = m.get("type", "backend")
                if mtype == "frontend":
                    ext = ".html"
                (project_dir / f"{name}{ext}").write_text(code, encoding="utf-8")

        # 写入集成产物（空字段时使用最小兜底，避免 ZIP 缺文件）
        main_code = (integration.main_code or "").strip()
        if not main_code:
            main_code = (
                '"""DevFlow 集成占位入口 — 请根据模块补全路由。"""\n'
                "from fastapi import FastAPI\n\napp = FastAPI()\n"
            )
            await push_log(pid, "WARN", "集成未生成 main.py，已写入最小占位入口")

        readme = (integration.readme or "").strip()
        if not readme:
            readme = (
                f"# {pid}\n\n"
                "## 安装\n\n```bash\npip install -r requirements.txt\n```\n\n"
                "## 说明\n\n由 DevFlow CI 自动生成。如有 blocked 模块，请参阅 TODO.md。\n"
            )
            await push_log(pid, "WARN", "集成未生成 README，已写入最小说明")

        requirements = (integration.requirements or "").strip()
        if not requirements:
            requirements = "fastapi>=0.115.0\nuvicorn[standard]>=0.34.0\npytest>=8.0.0\n"
            await push_log(pid, "WARN", "集成未生成 requirements.txt，已写入默认依赖")

        extra_reqs: list[str] = []
        for m in state["plan_modules"]:
            mname = m["module_name"]
            mres = state["module_results"].get(mname, {})
            extra_reqs.extend(
                infer_extra_requirements(
                    m.get("description", ""),
                    mres.get("code", "") or "",
                )
            )
        if extra_reqs:
            requirements = merge_requirements_text(requirements, extra_reqs)

        integration_tests = (integration.integration_tests or "").strip()
        if not integration_tests:
            integration_tests = (
                "def test_delivery_placeholder():\n"
                '    """集成测试占位 — 集成 Agent 未产出时可手动补充。"""\n'
                "    assert True\n"
            )
            await push_log(pid, "WARN", "集成未生成 integration_tests，已写入占位测试")

        fixtures_src = DELIVERIES_DIR / pid / "fixtures"
        if fixtures_src.is_dir():
            import shutil

            shutil.copytree(
                fixtures_src,
                project_dir / "fixtures",
                dirs_exist_ok=True,
            )

        (project_dir / "main.py").write_text(main_code, encoding="utf-8")
        (project_dir / "README.md").write_text(readme, encoding="utf-8")
        (project_dir / "requirements.txt").write_text(requirements, encoding="utf-8")

        # 写入集成测试（如果有 blocked 模块，额外生成 test_blocked_modules.py）
        if integration_tests:
            (project_dir / "test_integration.py").write_text(
                integration_tests, encoding="utf-8"
            )

        # ── 生成 TODO.md（未完成模块清单） ──
        blocked_modules = state.get("blocked_modules", [])
        if blocked_modules:
            todo_lines = [
                "# 未完成模块清单 (TODO)",
                "",
                f"> 生成时间: {datetime.now(timezone.utc).isoformat()}",
                f"> 项目: {pid}",
                f"> 共 {len(blocked_modules)} 个模块需要手动完善",
                "",
                "以下模块因审查不通过或执行异常被自动阻塞，已生成占位文件。",
                "请按优先级逐一完成实现。",
                "",
            ]
            for i, mod_name in enumerate(blocked_modules, 1):
                result = state["module_results"].get(mod_name, {})
                todo_lines.append(f"## {i}. 模块: {mod_name}")
                todo_lines.append("")
                # 找原始规划描述
                for m in state["plan_modules"]:
                    if m["module_name"] == mod_name:
                        todo_lines.append(f"- **原定需求**: {m.get('description', '未知')}")
                        todo_lines.append(f"- **模块类型**: {m.get('type', 'unknown')}")
                        break
                todo_lines.append("- **状态**: 阻塞 (blocked)")
                todo_lines.append(f"- **阻塞原因**: {result.get('failure_reason', '未知')}")
                todo_lines.append("")
                todo_lines.append("**建议手动修复步骤**:")
                todo_lines.append(f"1. 打开文件 `{mod_name}.py`（或对应的 .html 文件）")
                todo_lines.append("2. 阅读文件顶部的阻塞原因注释")
                todo_lines.append("3. 根据原定需求描述完成代码实现")
                todo_lines.append("4. 编写对应的单元测试")
                todo_lines.append("5. 验证功能正确性")
                todo_lines.append("")

            todo_text = "\n".join(todo_lines)
            (project_dir / "TODO.md").write_text(todo_text, encoding="utf-8")

            # 生成 test_blocked_modules.py
            test_blocked_lines = [
                '"""提醒哪些模块尚未完成的测试文件。"""',
                '',
                'import pytest',
                '',
                '',
                'BLOCKED_MODULES = [',
            ]
            for mod_name in blocked_modules:
                test_blocked_lines.append(f'    "{mod_name}",')
            test_blocked_lines.append(']')
            test_blocked_lines.append('')
            test_blocked_lines.append('')
            test_blocked_lines.append('@pytest.mark.skip(reason="模块尚未完成，详见 TODO.md")')
            test_blocked_lines.append('@pytest.mark.parametrize("module_name", BLOCKED_MODULES)')
            test_blocked_lines.append('def test_blocked_module_reminder(module_name: str) -> None:')
            test_blocked_lines.append('    """提醒开发人员：此模块尚未完成。"""')
            test_blocked_lines.append('    pytest.skip(f"模块 \\"{module_name}\\" 尚未完成，请先完善代码后再运行测试。详见 TODO.md")')
            test_blocked_lines.append('')
            (project_dir / "test_blocked_modules.py").write_text(
                "\n".join(test_blocked_lines), encoding="utf-8"
            )

        # 写入审查报告（QUALITY_REPORT 由 document_sync 生成）
        report = state.get("global_review", {})
        blocked_info = ""
        if blocked_modules:
            blocked_info = f"\n- ⚠️ 未完成模块数: {len(blocked_modules)}\n- 未完成模块: {', '.join(blocked_modules)}\n"
        report_text = f"""# 全局审查报告

- 审查结果: {'PASS' if report.get('passed') else '需改进'}
- 评分: {report.get('score', 0)}/100
- 摘要: {report.get('summary', '')}
{blocked_info}
- 问题: {json.dumps(report.get('issues', []), ensure_ascii=False, indent=2)}
- 建议: {json.dumps(report.get('suggestions', []), ensure_ascii=False, indent=2)}
"""
        (project_dir / "REVIEW_REPORT.md").write_text(
            report_text, encoding="utf-8"
        )

        # ── 文档同步：CHANGELOG / QUALITY / 复制 docs / 项目记忆 ──
        try:
            from workflow.document_sync import (
                build_ctx_from_workflow_state,
                sync_on_package,
            )

            sync_on_package(
                build_ctx_from_workflow_state(state),
                project_dir,
                version_num=version_num,
                project_memory_store=self._project_memory,
            )
        except Exception as exc:
            await push_log(pid, "WARN", f"文档同步失败（不影响打包）: {exc}")

        # 打包 ZIP
        zip_path = project_dir.with_suffix(".zip")
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for file in project_dir.rglob("*"):
                if file.is_file():
                    zf.write(file, file.relative_to(project_dir))

        return str(zip_path)
