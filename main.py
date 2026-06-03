"""DevFlow CI — 自动化多 Agent 编码工作流系统。

FastAPI 主入口，提供：
- REST API（20+ 端点，详见 docs/API.md）
- SSE 实时日志流 + AI token 流
- 静态文件服务（前端 UI）
- 需求对齐 + 上下文分析 + 多 Agent 工作流
- 永不卡死的工作流策略
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time as _time
import uuid
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from agents.alignment_agent import AlignmentAgent
from agents.business_planner import BusinessPlannerAgent
from agents.integrator import GlobalReviewerAgent, IntegratorAgent
from agents.module_agents import ModuleAgents
from agents.planner import PlannerAgent
from agents.repair_agent import RepairAgent
from agents.reviewer import ReviewerAgent
from config import (
    BUSINESS_MVP_MAX_MODULES,
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    DEEPSEEK_MODEL,
    DELIVERIES_DIR,
    MAX_HUMAN_FIXES,
    MEMORY_DIR,
    PROJECT_ROOT,
    STATIC_DIR,
)
from database.db import init_db
from database.models import (
    ModuleStatus,
    ModuleTask,
    Project,
    ProjectLog,
    ProjectStatus,
)
from memory.case_store import CaseStore
from memory.project_memory import ProjectMemoryStore
from workflow.error_logger import (
    clean_resolved_logs,
    get_error_stats,
    get_open_errors,
    resolve_error,
    write_error_log,
)
from workflow.executor import (
    WorkflowExecutor,
    WorkflowState,
    push_log,
    remove_log_queue,
    stream_logs,
)
from workflow.stream_relay import (
    get_stream_queue,
    push_ai_token,
    remove_stream_queue,
    stream_ai_tokens,
    stream_deepseek_call,
)

logger = logging.getLogger(__name__)

# ── 全局单例 ──────────────────────────────────────────────

_case_store = CaseStore()
_project_memory_store = ProjectMemoryStore()
_alignment_agent = AlignmentAgent()
_business_planner = BusinessPlannerAgent()
_planner = PlannerAgent(case_store=_case_store)
_module_agents = ModuleAgents(case_store=_case_store)
_reviewer = ReviewerAgent()
_integrator = IntegratorAgent()
_global_reviewer = GlobalReviewerAgent()
_repair_agent = RepairAgent()
_executor = WorkflowExecutor(
    planner=_planner,
    modules=_module_agents,
    reviewer=_reviewer,
    integrator=_integrator,
    global_reviewer=_global_reviewer,
    repair_agent=_repair_agent,
    alignment_agent=_alignment_agent,
    business_planner=_business_planner,
    case_store=_case_store,
    project_memory_store=_project_memory_store,
)

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


# ── 应用生命周期 ──────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用启动/关闭时执行。"""
    from logging_config import configure_logging
    configure_logging()
    await init_db()
    yield


app = FastAPI(
    title="DevFlow CI",
    description="自动化多 Agent 编码工作流系统 — 项目型开发伙伴",
    version="0.4.2",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 静态文件
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# ── 速率限制 ──────────────────────────────────────────
_rate_window = {}
_RATE_LIMIT = 60
_RATE_WINDOW = 60

async def _rate_limit_middleware(request, call_next):
    client = request.client.host if request.client else "unknown"
    now = _time.time()
    window = _rate_window.setdefault(client, [])
    window[:] = [t for t in window if now - t < _RATE_WINDOW]
    if len(window) >= _RATE_LIMIT:
        return JSONResponse(status_code=429, content={"detail": "请求过于频繁，请稍后再试", "retry_after": _RATE_WINDOW})
    window.append(now)
    return await call_next(request)

app.middleware("http")(_rate_limit_middleware)


# ── 请求模型 ──────────────────────────────────────────────

class CreateProjectRequest(BaseModel):
    """创建项目请求体。"""
    requirement: str = Field(default="", description="用户自然语言需求")
    directory: str | None = Field(default=None, description="项目目录绝对路径（选填）")
    mode: str = Field(default="auto", description="计划类型: auto/technical/business")
    force_new: bool = Field(default=False, description="强制创建新项目，跳过去重（重试场景使用）")


class UpdateDisplayNameRequest(BaseModel):
    """更新历史项目显示名称。"""
    display_name: str = Field(..., min_length=1, max_length=128, description="自定义标题")


class IterateProjectRequest(BaseModel):
    """开始下一轮迭代改进。"""
    addendum: str = Field(
        default="",
        description="本轮需求补充说明（可选）",
    )
    module_names: list[str] = Field(
        default_factory=list,
        description="指定补跑模块；为空则默认全部 blocked 模块",
    )


class RequirementAddendumRequest(BaseModel):
    """仅追加需求补充（不启动迭代）。"""
    text: str = Field(..., min_length=1, description="补充说明")


class ConfirmPlanRequest(BaseModel):
    """确认规划请求体。"""
    modules: list[dict[str, Any]] | None = Field(
        None, description="可选的修改后模块列表，为空则使用原始规划"
    )
    plan_choice: str | None = Field(
        None, description="方案选择: 'A' 或 'B'（多方案生成时有效）"
    )
    dependencies_override: list[dict[str, Any]] | None = Field(
        None, description="用户修改后的依赖清单"
    )


class FeedbackRequest(BaseModel):
    """人工反馈请求体。"""
    original_code: str = Field(default="", description="系统生成的代码片段")
    modified_code: str = Field(default="", description="人工修改后的代码片段")
    file: str = Field(default="", description="修改的文件路径")
    line_range: str = Field(default="", description="修改的行号范围")
    fix_type: str = Field(default="Bug修复", description="修改类型")
    description: str = Field(default="", description="修改说明")


class RollbackRequest(BaseModel):
    """回滚请求体。"""
    version: str = Field(default="", description="要回滚到的版本号，如 'v1'")


class CleanupProjectsRequest(BaseModel):
    """批量清理历史项目。"""
    statuses: list[str] = Field(
        default=["completed", "cancelled", "failed", "needs_review"],
        description="要删除的项目状态列表",
    )
    include_stale: bool = Field(
        default=True,
        description="是否包含停滞的非终态项目（planning/created/aligning 等）",
    )
    stale_minutes: int = Field(default=10, ge=1, le=1440)
    delete_deliveries: bool = Field(
        default=True,
        description="是否同时删除 deliveries 目录下的交付物（默认开启）",
    )


class DeliveryCleanupRequest(BaseModel):
    """按建议路径删除 deliveries 下的冗余交付物。"""
    paths: list[str] = Field(default_factory=list, description="要删除的相对路径列表")
    dry_run: bool = Field(default=False, description="仅预览，不实际删除")


_TERMINAL_PROJECT_STATUSES = {
    ProjectStatus.COMPLETED,
    ProjectStatus.FAILED,
    ProjectStatus.NEEDS_REVIEW,
    ProjectStatus.CANCELLED,
    ProjectStatus.FINALIZED,
}

_ACTIVE_PROJECT_STATUSES = {
    ProjectStatus.CREATED,
    ProjectStatus.ALIGNING,
    ProjectStatus.ALIGNED,
    ProjectStatus.PLANNING,
    ProjectStatus.PLAN_READY,
    ProjectStatus.EXECUTING,
    ProjectStatus.INTEGRATING,
    ProjectStatus.REVIEWING,
}


async def _delete_project_record(
    project_id: str,
    *,
    delete_deliveries: bool = True,
) -> bool:
    """删除单个项目记录及关联交付物/日志。返回是否删除成功。"""
    from sqlalchemy import delete, select

    from database.db import async_session_factory
    from database.models import ErrorLog, Project
    from workflow.project_cleanup import cleanup_delivery_artifacts, prune_human_fixes

    task = _running_tasks.pop(project_id, None)
    if task and not task.done():
        task.cancel()

    if delete_deliveries:
        cleanup_delivery_artifacts(project_id)
        prune_human_fixes(project_id)

    async with async_session_factory() as db:
        result = await db.execute(
            select(Project).where(Project.project_id == project_id)
        )
        project = result.scalar_one_or_none()
        if not project:
            return False
        await db.execute(
            delete(ErrorLog).where(ErrorLog.project_id == project_id)
        )
        await db.delete(project)
        await db.commit()

    remove_log_queue(project_id)
    return True


async def _all_project_ids() -> set[str]:
    """数据库中所有 project_id。"""
    from sqlalchemy import select

    from database.db import async_session_factory
    from database.models import Project

    async with async_session_factory() as db:
        result = await db.execute(select(Project.project_id))
        return {row[0] for row in result.all()}


# ── API 路由 ──────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    """前端首页。"""
    html_path = STATIC_DIR / "index.html"
    if html_path.exists():
        return HTMLResponse(html_path.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>DevFlow CI</h1><p>前端文件未找到。</p>")


@app.get("/favicon.ico", include_in_schema=False)
async def favicon() -> FileResponse:
    """浏览器默认请求的站点图标。"""
    path = STATIC_DIR / "favicon.ico"
    if not path.exists():
        raise HTTPException(status_code=404, detail="favicon not found")
    return FileResponse(path, media_type="image/x-icon")


@app.post("/api/projects")
async def create_project(
    body: CreateProjectRequest,
) -> dict[str, Any]:
    """创建新项目并启动后台工作流。

    POST /api/projects
    Body: {requirement: "...", directory: "D:/path/to/project" | null}

    交互规则：
    - 文字与目录至少一项（二者为同一需求的不同来源，对齐阶段合并分析）
    - 仅文字 → 纯文字需求
    - 仅目录 → 扫描目录资料/代码后对齐（不再注入固定「分析代码」占位文案）
    - 两者都有 → 文字指令 + 目录扫描结果合并分析
    """
    from database.db import async_session_factory

    requirement = body.requirement.strip()
    directory = body.directory.strip() if body.directory else ""

    # 验证：文字与目录至少有一项（二者合并为同一需求的不同来源）
    if not requirement and not directory:
        raise HTTPException(400, "请输入文字说明，或选择资料/代码目录（可两者同时填写）")

    from workflow.requirement_context import persist_requirement_text, user_instruction

    if directory and not os.path.isdir(directory):
        raise HTTPException(400, f"目录不存在或无效: {directory}")

    stored_requirement = persist_requirement_text(requirement, directory or None)
    agent_requirement = user_instruction(requirement)

    # ── 目录去重：同一目录只允许一个活跃项目（force_new 跳过）──
    from sqlalchemy import select as sql_select
    _terminal_states = {ProjectStatus.COMPLETED, ProjectStatus.FAILED, ProjectStatus.NEEDS_REVIEW, ProjectStatus.CANCELLED}
    # ★ 时间兜底：超过此时间未更新的非终态项目视为僵尸，自动释放
    _zombie_timeout = timedelta(minutes=10)
    if not body.force_new:
        async with async_session_factory() as db:
            existing = await db.execute(
                sql_select(Project).where(
                    Project.directory == directory,
                    Project.status.not_in(_terminal_states),
                ).order_by(Project.created_at.desc())
            )
            dup = existing.scalars().first()
            if dup:
                should_replace = False
                replace_reason = ""

                # 检查1：项目是否已超时（僵尸项目）
                if dup.updated_at and (datetime.now(timezone.utc) - dup.updated_at) > _zombie_timeout:
                    should_replace = True
                    elapsed = (datetime.now(timezone.utc) - dup.updated_at).seconds // 60
                    replace_reason = f"旧项目已停滞 {elapsed} 分钟，自动标记为失效"

                # 检查2：项目 alignment 是 insufficient_info
                if not should_replace and dup.alignment_json:
                    try:
                        al = json.loads(dup.alignment_json)
                        if al.get("status") == "insufficient_info":
                            should_replace = True
                            replace_reason = "旧项目因文档信息不足被标记为待审查"
                    except (json.JSONDecodeError, TypeError):
                        pass

                if should_replace:
                    dup.status = ProjectStatus.NEEDS_REVIEW
                    await db.commit()
                    await push_log(dup.project_id, "INFO", f"{replace_reason}，已创建新项目")
                    # 不返回，继续创建新项目
                else:
                    # 目录已有活跃项目，直接复用
                    return {"project_id": dup.project_id, "reused": True}

    project_id = f"proj-{uuid.uuid4().hex[:12]}"

    # 持久化项目记录
    async with async_session_factory() as db:
        project = Project(
            project_id=project_id,
            requirement=stored_requirement,
            display_name=_default_display_name(stored_requirement),
            directory=directory if directory else None,
            status=ProjectStatus.CREATED,
        )
        db.add(project)
        await db.commit()

    # 后台启动统一工作流
    state: WorkflowState = {
        "project_id": project_id,
        "requirement": agent_requirement,
        "directory": directory,
        "project_context": "",
        "alignment_result": {},
        "alternative_alignment": None,
        "plan_json": "",
        "plan_modules": [],
        "module_results": {},
        "module_order": [],
        "blocked_modules": [],
        "integration_result": {},
        "global_review": {},
        "delivery_path": "",
        "status": "created",
        "errors": [],
        "force_mode": body.mode if body.mode != "auto" else "",
    }

    # 所有项目统一走完整工作流
    _register_task(project_id, _run_workflow(project_id, state))

    return {"project_id": project_id}


@app.get("/api/projects/history")
async def get_history(limit: int = 20) -> list[dict[str, Any]]:
    """获取历史项目列表。

    GET /api/projects/history?limit=20
    """
    from sqlalchemy import desc, select

    from database.db import async_session_factory

    async with async_session_factory() as db:
        result = await db.execute(
            select(Project)
            .order_by(desc(Project.created_at))
            .limit(limit)
        )
        projects = result.scalars().all()

        dir_counts: dict[str, int] = {}
        dir_active: dict[str, str] = {}
        for p in projects:
            if not p.directory:
                continue
            dir_counts[p.directory] = dir_counts.get(p.directory, 0) + 1
            if (
                p.status not in _TERMINAL_PROJECT_STATUSES
                and not _is_stale_project(p)
                and p.directory not in dir_active
            ):
                dir_active[p.directory] = p.project_id

        return [
            {
                "project_id": p.project_id,
                "display_name": p.display_name or "",
                "requirement": p.requirement[:100] + "..."
                if len(p.requirement) > 100
                else p.requirement,
                "status": p.status.value,
                "directory": p.directory,
                "blocked_count": p.blocked_count,
                "iteration": p.iteration or 1,
                "created_at": p.created_at.isoformat(),
                "updated_at": p.updated_at.isoformat(),
                "is_stale": _is_stale_project(p),
                "directory_entry_count": dir_counts.get(p.directory, 0)
                if p.directory
                else 0,
                "directory_active_id": dir_active.get(p.directory)
                if p.directory
                else None,
            }
            for p in projects
        ]


def _default_display_name(requirement: str, max_len: int = 80) -> str | None:
    """从需求首行生成默认历史标题。"""
    text = (requirement or "").strip()
    if not text:
        return None
    first_line = text.splitlines()[0].strip()
    if not first_line:
        return None
    if len(first_line) > max_len:
        return first_line[: max_len - 1] + "…"
    return first_line


def _is_stale_project(project: Project) -> bool:
    """非终态且长时间无更新视为停滞。"""
    if project.status in _TERMINAL_PROJECT_STATUSES:
        return False
    if not project.updated_at:
        return False
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=10)
    updated = project.updated_at
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=timezone.utc)
    return updated < cutoff


async def _delete_project_impl(
    project_id: str,
    *,
    delete_deliveries: bool = True,
) -> dict[str, Any]:
    """删除历史项目（运行中项目会先终止）。"""
    from sqlalchemy import select

    from database.db import async_session_factory

    async with async_session_factory() as db:
        result = await db.execute(
            select(Project).where(Project.project_id == project_id)
        )
        project = result.scalar_one_or_none()
        if not project:
            raise HTTPException(404, "项目不存在")

        if project.status in _ACTIVE_PROJECT_STATUSES:
            task = _running_tasks.get(project_id)
            if task and not task.done():
                task.cancel()
                _running_tasks.pop(project_id, None)
            project.status = ProjectStatus.CANCELLED
            await db.commit()

    deleted = await _delete_project_record(
        project_id, delete_deliveries=delete_deliveries
    )
    if not deleted:
        raise HTTPException(404, "项目不存在")

    return {"deleted": True, "project_id": project_id}


@app.delete("/api/projects/{project_id}")
async def delete_project(
    project_id: str,
    delete_deliveries: bool = True,
) -> dict[str, Any]:
    """DELETE /api/projects/{project_id}?delete_deliveries=true"""
    return await _delete_project_impl(
        project_id, delete_deliveries=delete_deliveries
    )


@app.post("/api/projects/{project_id}/delete")
async def delete_project_post(
    project_id: str,
    delete_deliveries: bool = True,
) -> dict[str, Any]:
    """POST /api/projects/{project_id}/delete?delete_deliveries=true（与 DELETE 等效）"""
    return await _delete_project_impl(
        project_id, delete_deliveries=delete_deliveries
    )


@app.post("/api/projects/cleanup")
async def cleanup_projects(body: CleanupProjectsRequest) -> dict[str, Any]:
    """批量清理历史项目。

    POST /api/projects/cleanup
    """
    from sqlalchemy import select

    from database.db import async_session_factory

    allowed = {s.value for s in ProjectStatus}
    target_statuses = {s for s in body.statuses if s in allowed}
    if not target_statuses and not body.include_stale:
        raise HTTPException(400, "请指定要清理的状态或启用 include_stale")

    stale_cutoff = datetime.now(timezone.utc) - timedelta(minutes=body.stale_minutes)
    deleted: list[str] = []

    async with async_session_factory() as db:
        result = await db.execute(select(Project))
        projects = list(result.scalars().all())

    for project in projects:
        if project.status in _ACTIVE_PROJECT_STATUSES:
            if _running_tasks.get(project.project_id):
                continue
        should_delete = project.status.value in target_statuses
        if not should_delete and body.include_stale:
            updated = project.updated_at
            if updated and updated.tzinfo is None:
                updated = updated.replace(tzinfo=timezone.utc)
            if (
                project.status not in _TERMINAL_PROJECT_STATUSES
                and updated
                and updated < stale_cutoff
            ):
                should_delete = True
        if should_delete:
            if await _delete_project_record(
                project.project_id, delete_deliveries=body.delete_deliveries
            ):
                deleted.append(project.project_id)

    return {"deleted_count": len(deleted), "deleted_ids": deleted}


@app.get("/api/projects/{project_id}")
async def get_project(project_id: str) -> dict[str, Any]:
    """获取项目完整状态。

    GET /api/projects/{project_id}
    """
    from workflow.project_snapshot import fetch_project_snapshot

    snapshot = await fetch_project_snapshot(project_id)
    if not snapshot:
        raise HTTPException(404, "项目不存在")
    return snapshot


@app.patch("/api/projects/{project_id}/display_name")
async def update_project_display_name(
    project_id: str,
    body: UpdateDisplayNameRequest,
) -> dict[str, Any]:
    """更新历史项目自定义标题。

    PATCH /api/projects/{project_id}/display_name
    """
    from sqlalchemy import select

    from database.db import async_session_factory

    name = body.display_name.strip()
    if not name:
        raise HTTPException(400, "标题不能为空")

    async with async_session_factory() as db:
        result = await db.execute(
            select(Project).where(Project.project_id == project_id)
        )
        project = result.scalar_one_or_none()
        if not project:
            raise HTTPException(404, "项目不存在")
        project.display_name = name[:128]
        await db.commit()

    return {
        "project_id": project_id,
        "display_name": name[:128],
    }


@app.post("/api/projects/{project_id}/iterate")
async def iterate_project(
    project_id: str,
    body: IterateProjectRequest,
) -> dict[str, Any]:
    """在同一项目上开始下一轮迭代（补跑 blocked / 重新集成交付）。

    POST /api/projects/{project_id}/iterate
    """
    from sqlalchemy import select

    from database.db import async_session_factory
    from workflow.requirement_context import (
        append_requirement_addendum,
        build_effective_requirement,
        build_iteration_context,
        parse_requirement_addenda,
    )
    from workflow.state_builder import build_workflow_state_from_db

    if _running_tasks.get(project_id):
        raise HTTPException(409, "项目正在运行中，请稍后再试")

    async with async_session_factory() as db:
        result = await db.execute(
            select(Project).where(Project.project_id == project_id)
        )
        project = result.scalar_one_or_none()
        if not project:
            raise HTTPException(404, "项目不存在")
        if project.status == ProjectStatus.FINALIZED:
            raise HTTPException(400, "项目已标记定稿，无法继续迭代")
        if project.status not in {
            ProjectStatus.COMPLETED,
            ProjectStatus.NEEDS_REVIEW,
        }:
            raise HTTPException(
                400,
                "仅已完成或需审查的项目可开始迭代（当前状态: "
                f"{project.status.value}）",
            )
        if not project.plan_json:
            raise HTTPException(400, "项目缺少规划数据，无法迭代")

        user_addendum = (body.addendum or "").strip()
        new_iteration = (project.iteration or 1) + 1
        project.iteration = new_iteration
        project.status = ProjectStatus.EXECUTING
        await db.commit()

    built = await build_workflow_state_from_db(project_id)
    if not built:
        raise HTTPException(404, "无法重建项目状态")
    state, _project = built
    state["iteration"] = new_iteration

    blocked = list(state.get("blocked_modules") or [])
    if body.module_names:
        target_modules = [n.strip() for n in body.module_names if n.strip()]
        reintegrate_only = False
    elif blocked:
        target_modules = blocked
        reintegrate_only = False
    elif user_addendum:
        target_modules = []
        reintegrate_only = True
    else:
        raise HTTPException(
            400,
            "请填写需求补充说明，或指定要补跑的模块（当前无 blocked 模块）",
        )

    from workflow.iteration_automation import (
        build_auto_iterate_addendum,
        merge_addenda,
    )

    name_to_module = {m["module_name"]: m for m in state.get("plan_modules", [])}
    auto_parts: list[str] = []
    if target_modules and not reintegrate_only:
        for name in target_modules:
            prior = state.get("module_results", {}).get(name, {})
            issues = prior.get("errors") or []
            mod = name_to_module.get(name, {})
            auto_parts.append(
                build_auto_iterate_addendum(
                    name,
                    mod.get("description", ""),
                    issues,
                    iteration=new_iteration,
                    failure_reason=prior.get("failure_reason", ""),
                )
            )
    auto_addendum = "\n\n".join(auto_parts)
    addendum_text = merge_addenda(user_addendum, auto_addendum)

    if addendum_text:
        async with async_session_factory() as db:
            result = await db.execute(
                select(Project).where(Project.project_id == project_id)
            )
            proj = result.scalar_one_or_none()
            if proj:
                proj.requirement_addendum_json = append_requirement_addendum(
                    proj.requirement_addendum_json,
                    text=addendum_text,
                    round_num=new_iteration,
                )
                await db.commit()
        state["requirement_addendum_json"] = append_requirement_addendum(
            _project.requirement_addendum_json,
            text=addendum_text,
            round_num=new_iteration,
        )

    addenda = parse_requirement_addenda(state.get("requirement_addendum_json"))
    state["requirement"] = build_effective_requirement(
        _project.requirement, addenda
    )
    state["base_requirement"] = _project.requirement
    state["project_context"] = build_iteration_context(
        iteration=new_iteration,
        addendum_text=addendum_text,
        global_review=state.get("global_review"),
    )

    from workflow.document_sync import ctx_from_project, sync_iteration_start

    doc_ctx = ctx_from_project(_project, plan_modules=state.get("plan_modules"))
    doc_ctx.iteration = new_iteration
    sync_iteration_start(
        doc_ctx,
        addendum_text=addendum_text,
        target_modules=target_modules,
        reintegrate_only=reintegrate_only,
    )

    await push_log(
        project_id,
        "INFO",
        f"🔄 用户启动第 {new_iteration} 轮迭代"
        + (f"，补充：{addendum_text[:80]}…" if len(addendum_text) > 80
           else (f"，补充：{addendum_text}" if addendum_text else ""))
        + ("（含工作流自动生成窄 scope）" if auto_addendum and not user_addendum else ""),
    )

    _register_task(
        project_id,
        _run_improvement(
            project_id,
            state,
            target_modules,
            reintegrate_only=reintegrate_only,
        ),
    )

    return {
        "project_id": project_id,
        "iteration": new_iteration,
        "status": "executing",
        "target_modules": target_modules,
        "reintegrate_only": reintegrate_only,
    }


@app.patch("/api/projects/{project_id}/requirement_addendum")
async def append_requirement_addendum_only(
    project_id: str,
    body: RequirementAddendumRequest,
) -> dict[str, Any]:
    """追加需求补充记录（不立即启动迭代）。"""
    from sqlalchemy import select

    from database.db import async_session_factory
    from workflow.requirement_context import append_requirement_addendum

    text = body.text.strip()
    if not text:
        raise HTTPException(400, "补充内容不能为空")

    async with async_session_factory() as db:
        result = await db.execute(
            select(Project).where(Project.project_id == project_id)
        )
        project = result.scalar_one_or_none()
        if not project:
            raise HTTPException(404, "项目不存在")
        round_num = (project.iteration or 1) + 1
        project.requirement_addendum_json = append_requirement_addendum(
            project.requirement_addendum_json,
            text=text,
            round_num=round_num,
        )
        await db.commit()

    return {"project_id": project_id, "saved": True}


@app.post("/api/projects/{project_id}/finalize")
async def finalize_project(project_id: str) -> dict[str, Any]:
    """标记项目定稿，不再提示继续迭代；同步 ACCEPTANCE 与平台 LEARNINGS。"""
    from sqlalchemy import select

    from database.db import async_session_factory
    from workflow.document_sync import build_ctx_from_workflow_state, sync_on_finalize
    from workflow.state_builder import build_workflow_state_from_db

    async with async_session_factory() as db:
        result = await db.execute(
            select(Project).where(Project.project_id == project_id)
        )
        project = result.scalar_one_or_none()
        if not project:
            raise HTTPException(404, "项目不存在")
        if project.status not in {
            ProjectStatus.COMPLETED,
            ProjectStatus.NEEDS_REVIEW,
        }:
            raise HTTPException(400, "仅已完成的项目可标记定稿")
        project.status = ProjectStatus.FINALIZED
        await db.commit()
        delivery_path = project.delivery_path or ""

    try:
        built = await build_workflow_state_from_db(project_id)
        if built:
            state, _ = built
            ctx = build_ctx_from_workflow_state(state)
            sync_on_finalize(ctx, delivery_path=delivery_path)
            await push_log(project_id, "INFO", "📄 已生成 ACCEPTANCE.md 并同步定稿文档")
    except Exception as exc:
        await push_log(project_id, "WARN", f"定稿文档同步失败: {exc}")

    return {"project_id": project_id, "status": "finalized"}


@app.get("/api/projects/{project_id}/logs/recent")
async def get_recent_logs(project_id: str, limit: int = 150) -> list[dict[str, Any]]:
    """获取项目历史日志（非 SSE，用于终态回顾）。

    GET /api/projects/{project_id}/logs/recent?limit=150
    """
    from sqlalchemy import desc, select

    from database.db import async_session_factory

    limit = max(1, min(limit, 500))
    async with async_session_factory() as db:
        proj = await db.execute(
            select(Project).where(Project.project_id == project_id)
        )
        project = proj.scalar_one_or_none()
        if not project:
            raise HTTPException(404, "项目不存在")

        result = await db.execute(
            select(ProjectLog)
            .where(ProjectLog.project_id_fk == project.id)
            .order_by(desc(ProjectLog.timestamp))
            .limit(limit)
        )
        rows = list(reversed(result.scalars().all()))
        return [
            {
                "level": row.level,
                "message": row.message,
                "module_name": row.module_name or "",
                "timestamp": row.timestamp.isoformat() if row.timestamp else "",
            }
            for row in rows
        ]


@app.get("/api/projects/{project_id}/logs")
async def get_logs_sse(project_id: str) -> StreamingResponse:
    """实时日志流（SSE）。

    GET /api/projects/{project_id}/logs
    """
    from sqlalchemy import select

    from database.db import async_session_factory

    # 检查项目是否存在
    async with async_session_factory() as db:
        result = await db.execute(
            select(Project).where(Project.project_id == project_id)
        )
        if not result.scalar_one_or_none():
            raise HTTPException(404, "项目不存在")

    async def event_generator():
        async for entry in stream_logs(project_id):
            yield f"data: {json.dumps(entry, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/projects/{project_id}/stream-ai")
async def get_ai_stream(project_id: str) -> StreamingResponse:
    """实时 AI 输出流（SSE）—— 接收 AI token 和系统通知。

    GET /api/projects/{project_id}/stream-ai

    推送事件类型：
    - type: "token"       — AI 实时输出的 token 片段
    - type: "notification" — 系统通知（Agent 开始/完成）
    - type: "done"        — 当前 AI 任务完成
    - type: "error"       — AI 调用异常
    - type: "heartbeat"   — 保持连接的心跳
    - agent: 代理标识（用于前端颜色标签）
    """
    from sqlalchemy import select

    from database.db import async_session_factory

    # 检查项目是否存在
    async with async_session_factory() as db:
        result = await db.execute(
            select(Project).where(Project.project_id == project_id)
        )
        if not result.scalar_one_or_none():
            raise HTTPException(404, "项目不存在")

    async def ai_event_generator():
        async for entry in stream_ai_tokens(project_id):
            yield f"data: {json.dumps(entry, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        ai_event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/projects/{project_id}/business_tech_preview")
async def business_tech_preview(project_id: str) -> dict[str, Any]:
    """商业计划确认前预览：将生成的技术需求与预计模块数。

    GET /api/projects/{project_id}/business_tech_preview
    """
    from sqlalchemy import select

    from database.db import async_session_factory

    async with async_session_factory() as db:
        result = await db.execute(
            select(Project).where(Project.project_id == project_id)
        )
        project = result.scalar_one_or_none()
        if not project:
            raise HTTPException(404, "项目不存在")

        if project.status != ProjectStatus.ALIGNED:
            raise HTTPException(400, "仅在对齐完成待确认（aligned）时可预览")

        alignment_result = (
            json.loads(project.alignment_json) if project.alignment_json else {}
        )
        if alignment_result.get("plan_type") != "business":
            raise HTTPException(400, "当前项目不是商业计划模式")

        tech_requirement, mvp_max_modules = _build_business_tech_requirement(
            project.requirement, alignment_result
        )
        return {
            "tech_requirement": tech_requirement,
            "mvp_max_modules": mvp_max_modules,
            "estimated_modules": mvp_max_modules,
            "plan_type": "business",
        }


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


@app.post("/api/projects/{project_id}/confirm_plan")
async def confirm_plan(
    project_id: str,
    body: ConfirmPlanRequest,
) -> dict[str, Any]:
    """确认需求对齐计划或执行规划后继续执行。

    POST /api/projects/{project_id}/confirm_plan
    Body: {modules: [...], plan_choice: "A"|"B"}  optional

    两种场景：
    1. 项目状态为 aligned → 确认需求对齐计划，进入规划+执行阶段
    2. 项目状态为 plan_ready → 确认执行规划，进入执行阶段
    """
    from sqlalchemy import select

    from database.db import async_session_factory

    async with async_session_factory() as db:
        result = await db.execute(
            select(Project).where(Project.project_id == project_id)
        )
        project = result.scalar_one_or_none()
        if not project:
            raise HTTPException(404, "项目不存在")

        valid_statuses = {ProjectStatus.ALIGNED, ProjectStatus.PLAN_READY}
        if project.status not in valid_statuses:
            raise HTTPException(400, "项目不在等待确认状态（需为 aligned 或 plan_ready）")

        # ── 场景1：对齐确认 → 保存对齐计划，进入规划 ──
        if project.status == ProjectStatus.ALIGNED:
            # 如果用户修改了对齐计划中的模块
            if body.modules:
                alignment_data = json.loads(project.alignment_json) if project.alignment_json else {}
                alignment_data["plan"] = body.modules
                project.alignment_json = json.dumps(alignment_data, ensure_ascii=False)

            # 记录假设和风险到 decisions.md（简化记录）
            _record_alignment_to_decisions(project_id, project.alignment_json)

            # ── 商业计划模式：确认后提取需求建议，继续走技术规划+开发管线 ──
            alignment_result = json.loads(project.alignment_json) if project.alignment_json else {}
            mvp_max_modules: int | None = None
            if alignment_result.get("plan_type") == "business":
                original_requirement = project.requirement
                tech_requirement, mvp_max_modules = _build_business_tech_requirement(
                    original_requirement, alignment_result
                )
                project.requirement = tech_requirement[:2000]
                alignment_result["mvp_max_modules"] = mvp_max_modules
                project.alignment_json = json.dumps(
                    alignment_result, ensure_ascii=False
                )
                await push_log(
                    project_id, "INFO",
                    f"商业计划已确认，提取技术需求（MVP≤{mvp_max_modules}模块）："
                    f"{tech_requirement[:200]}...",
                )

            # 文档同步：PRODUCT + DELIVERY_PLAN
            from workflow.document_sync import ctx_from_project, sync_after_alignment_confirm

            sync_after_alignment_confirm(
                ctx_from_project(project, alignment=alignment_result)
            )
            _write_delivery_plan(project_id, project)

            # 用户选方案
            project.status = ProjectStatus.PLANNING
            await db.commit()

            # 启动后续阶段
            state: WorkflowState = {
                "project_id": project_id,
                "requirement": project.requirement,
                "directory": project.directory or "",
                "project_context": "",
                "alignment_result": alignment_result,
                "alternative_alignment": None,
                "plan_json": "",
                "plan_modules": [],
                "module_results": {},
                "module_order": [],
                "blocked_modules": [],
                "integration_result": {},
                "global_review": {},
                "delivery_path": "",
                "status": "planning",
                "errors": [],
            }
            if mvp_max_modules is not None:
                state["mvp_max_modules"] = mvp_max_modules

            _register_task(project_id, _run_workflow_after_alignment(project_id, state))
            return {"status": "planning", "message": "需求对齐已确认，进入规划阶段"}

        # ── 场景2：规划确认 → 进入执行（已有逻辑） ──
        if body.modules:
            project.plan_json = json.dumps(
                {"modules": body.modules}, ensure_ascii=False
            )
            # 更新模块表
            # 删除旧模块
            from sqlalchemy import delete as sql_delete
            await db.execute(
                sql_delete(ModuleTask).where(
                    ModuleTask.project_id_fk == project.id
                )
            )
            # 创建新模块
            for m in body.modules:
                mod = ModuleTask(
                    project_id_fk=project.id,
                    module_name=m["module_name"],
                    description=m.get("description", ""),
                    dependencies=json.dumps(
                        m.get("dependencies", []), ensure_ascii=False
                    ),
                    module_type=m.get("type", "backend"),
                    status=ModuleStatus.PENDING,
                )
                db.add(mod)

        project.status = ProjectStatus.EXECUTING
        await db.commit()

        alignment_result = (
            json.loads(project.alignment_json) if project.alignment_json else {}
        )
        mvp_max_modules = _resolve_mvp_max_modules_from_project(
            project, alignment_result
        )

    # 启动执行
    state: WorkflowState = {
        "project_id": project_id,
        "requirement": project.requirement,
        "directory": project.directory or "",
        "project_context": "",
        "alignment_result": alignment_result,
        "alternative_alignment": None,
        "plan_json": project.plan_json or "",
        "plan_modules": (
            json.loads(project.plan_json).get("modules", [])
            if project.plan_json
            else []
        ),
        "module_results": {},
        "module_order": [],
        "blocked_modules": [],
        "integration_result": {},
        "global_review": {},
        "delivery_path": "",
        "status": "executing",
        "errors": [],
    }
    if mvp_max_modules is not None:
        state["mvp_max_modules"] = mvp_max_modules

    _register_task(project_id, _run_execution(project_id, state))

    return {"status": "executing"}


@app.post("/api/projects/{project_id}/cancel")
async def cancel_project(project_id: str) -> dict[str, Any]:
    """终止正在运行的项目。

    POST /api/projects/{project_id}/cancel
    """
    from sqlalchemy import select

    from database.db import async_session_factory

    # 取消后台任务
    task = _running_tasks.pop(project_id, None)
    if task and not task.done():
        task.cancel()
        await push_log(project_id, "WARN", "🛑 用户手动终止项目")

    # 更新数据库状态
    async with async_session_factory() as db:
        result = await db.execute(select(Project).where(Project.project_id == project_id))
        project = result.scalar_one_or_none()
        if project:
            if project.status in {ProjectStatus.COMPLETED, ProjectStatus.CANCELLED, ProjectStatus.FINALIZED}:
                raise HTTPException(400, f"项目已处于终态 {project.status.value}，无需取消")
            project.status = ProjectStatus.CANCELLED
            await db.commit()

    return {"status": "cancelled", "project_id": project_id}


@app.get("/api/projects/{project_id}/download")
async def download_project(project_id: str) -> FileResponse:
    """下载项目 ZIP 包。

    GET /api/projects/{project_id}/download
    """
    from sqlalchemy import select

    from database.db import async_session_factory
    from database.models import Project
    from workflow.delivery_paths import resolve_delivery_zip

    delivery_path: str | None = None
    async with async_session_factory() as db:
        result = await db.execute(
            select(Project).where(Project.project_id == project_id)
        )
        project = result.scalar_one_or_none()
        if project:
            delivery_path = project.delivery_path

    zip_path = resolve_delivery_zip(project_id, delivery_path)
    if not zip_path:
        raise HTTPException(404, "交付物尚未生成")

    return FileResponse(
        path=str(zip_path),
        filename=f"{project_id}.zip",
        media_type="application/zip",
    )


@app.get("/api/health")
async def health() -> dict[str, str]:
    """健康检查。"""
    return {
        "status": "ok",
        "cases_count": str(_case_store.count),
        "api_version": "0.4.9",
    }


# ── 文件系统浏览 API ────────────────────────────────────

@app.get("/api/fs/drives")
async def list_drives() -> dict[str, Any]:
    """列出可用盘符（Windows）或根目录（Unix）。

    GET /api/fs/drives
    """
    import platform
    import string as _string

    system = platform.system()
    if system == "Windows":
        drives = []
        for letter in _string.ascii_uppercase:
            root = f"{letter}:\\"
            if os.path.exists(root):
                drives.append({"name": root, "path": root, "type": "drive"})
        return {"drives": drives, "separator": "\\", "system": "Windows"}
    else:
        root = Path("/")
        try:
            entries = []
            for p in sorted(root.iterdir()):
                if p.is_dir() and not p.name.startswith("."):
                    entries.append({
                        "name": p.name,
                        "path": str(p),
                        "type": "dir",
                    })
            return {"drives": entries, "separator": "/", "system": system}
        except PermissionError:
            return {"drives": [{"name": "/", "path": "/", "type": "dir"}], "separator": "/", "system": system}


@app.get("/api/fs/browse")
async def browse_directory(path: str = "") -> dict[str, Any]:
    """浏览指定路径下的子目录。

    GET /api/fs/browse?path=C:\\Users
    返回该路径下的所有子目录（不包含文件）。
    """
    if not path:
        return await list_drives()

    p = Path(path)
    if not p.exists():
        raise HTTPException(404, f"路径不存在: {path}")
    if not p.is_dir():
        raise HTTPException(400, f"路径不是目录: {path}")

    try:
        entries = []
        for item in sorted(p.iterdir()):
            if item.is_dir() and not item.name.startswith("."):
                entries.append({
                    "name": item.name,
                    "path": str(item),
                    "type": "dir",
                })
        parent = str(p.parent) if p.parent != p else None
        return {
            "current": str(p),
            "parent": parent,
            "entries": entries,
        }
    except PermissionError as e:
        raise HTTPException(403, f"没有权限访问: {path}") from e


@app.get("/api/fs/quick-access")
async def quick_access() -> dict[str, Any]:
    """返回用户的常用快捷路径（桌面、文档、下载等）。

    GET /api/fs/quick-access
    类似 Windows 资源管理器左侧快速访问栏。
    """
    home = Path.home()
    entries = []

    # 此电脑
    entries.append({"name": "此电脑", "path": "", "icon": "💻", "type": "root"})

    # 用户主目录
    entries.append({"name": home.name or str(home), "path": str(home), "icon": "👤", "type": "home"})

    # 常用文件夹（仅添加存在的）
    quick_dirs = [
        ("桌面", home / "Desktop"),
        ("文档", home / "Documents"),
        ("下载", home / "Downloads"),
        ("图片", home / "Pictures"),
        ("音乐", home / "Music"),
        ("视频", home / "Videos"),
        ("OneDrive", home / "OneDrive"),
    ]
    for name, p in quick_dirs:
        if p.exists() and p.is_dir():
            entries.append({"name": name, "path": str(p), "icon": "📁", "type": "quick"})

    return {"entries": entries}


# ── 版本管理 API ────────────────────────────────────────

@app.get("/api/projects/{project_id}/versions")
async def get_versions(project_id: str) -> dict[str, Any]:
    """获取项目的版本列表。"""
    base_dir = DELIVERIES_DIR / project_id
    if not base_dir.exists():
        return {"project_id": project_id, "versions": []}
    existing = sorted([
        d.name for d in base_dir.iterdir()
        if d.is_dir() and d.name.startswith("v") and d.name[1:].isdigit()
    ], key=lambda x: int(x[1:]))
    return {"project_id": project_id, "versions": existing, "current": existing[-1] if existing else None}


@app.post("/api/projects/{project_id}/rollback")
async def rollback_version(project_id: str, body: RollbackRequest) -> dict[str, Any]:
    """回滚到指定版本。"""
    base_dir = DELIVERIES_DIR / project_id
    target = base_dir / body.version
    if not target.exists():
        raise HTTPException(404, f"版本 {body.version} 不存在")
    # 复制最新版本
    latest_num = max([int(d.name[1:]) for d in base_dir.iterdir() if d.is_dir() and d.name.startswith("v") and d.name[1:].isdigit()] or [0])
    new_ver = base_dir / f"v{latest_num + 1}"
    import shutil
    shutil.copytree(target, new_ver)
    await push_log(project_id, "INFO", f"从 {body.version} 回滚，创建新版本 v{latest_num + 1}")
    return {"status": "rolled_back", "from": body.version, "to": f"v{latest_num + 1}"}


# ── 反馈学习 API ────────────────────────────────────────

@app.post("/api/projects/{project_id}/feedback")
async def submit_feedback(project_id: str, body: FeedbackRequest) -> dict[str, Any]:
    """提交人工修改反馈。"""
    fixes_file = MEMORY_DIR / "human_fixes.json"
    try:
        data = json.loads(fixes_file.read_text(encoding="utf-8")) if fixes_file.exists() else {"fixes": []}
    except Exception as exc:
        logger.warning("读取 human_fixes.json 失败: %s", exc)
        data = {"fixes": []}
    data["fixes"].append({
        "original_code": body.original_code[:500],
        "modified_code": body.modified_code[:500],
        "file": body.file,
        "line_range": body.line_range,
        "fix_type": body.fix_type,
        "description": body.description[:200],
        "project_id": project_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    if len(data["fixes"]) > MAX_HUMAN_FIXES:
        data["fixes"] = data["fixes"][-MAX_HUMAN_FIXES:]
    fixes_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    await push_log(project_id, "SUCCESS", f"人工反馈已记录: {body.fix_type}")
    return {"status": "recorded", "total_fixes": len(data["fixes"])}


@app.get("/api/projects/{project_id}/preferences")
async def get_preference_report(project_id: str) -> dict[str, Any]:
    """生成偏好学习报告。"""
    fixes_file = MEMORY_DIR / "human_fixes.json"
    if not fixes_file.exists():
        return {"fixes": [], "summary": "暂无人工修改记录"}
    data = json.loads(fixes_file.read_text(encoding="utf-8"))
    fixes = data.get("fixes", [])
    types = {}
    for f in fixes:
        t = f.get("fix_type", "Other")
        types[t] = types.get(t, 0) + 1
    return {
        "total_fixes": len(fixes),
        "by_type": types,
        "recent_fixes": fixes[-10:],
        "summary": f"记录了 {len(fixes)} 次人工修改，最常见的类型: {max(types, key=types.get) if types else '无'} ({types.get(max(types, key=types.get), 0) if types else 0}次)",
    }


# ── 维护 API ────────────────────────────────────────────────


@app.get("/api/maintenance/error-stats")
async def get_error_stats_endpoint(project_id: str | None = None) -> dict[str, Any]:
    """获取错误统计与修复进度。

    GET /api/maintenance/error-stats?project_id=xxx
    """
    stats = await get_error_stats(project_id=project_id)
    return stats


@app.post("/api/maintenance/analyze-errors")
async def analyze_errors_endpoint(project_id: str | None = None) -> dict[str, Any]:
    """查询 open 错误日志，按模块聚合，调用 RepairAgent/LLM 生成批量修复建议。

    POST /api/maintenance/analyze-errors
    Body (可选): {"project_id": "proj-xxx"}
    """
    errors = await get_open_errors(project_id=project_id, limit=100)
    if not errors:
        return {"status": "no_errors", "errors_analyzed": 0, "fixes_applied": 0,
                "summary": "没有找到待处理错误"}

    # ── 按模块聚合 ──
    module_errors: dict[str, list[dict[str, Any]]] = {}
    for e in errors:
        mod = e.get("module_name") or "unknown"
        module_errors.setdefault(mod, []).append(e)

    analyzed_modules = len(module_errors)
    fixes_applied = 0
    fix_suggestions: list[str] = []

    # ── 调用 LLM 分析错误模式 ──
    for mod_name, mod_errs in list(module_errors.items())[:10]:
        error_messages = "\n".join(
            f"- [{e.get('error_type', 'unknown')}] {e.get('message', '')[:200]}"
            for e in mod_errs[:5]
        )

        analysis_prompt = f"""你是一位资深运维工程师。请分析以下模块的错误模式并给出批量修复建议。

模块: {mod_name}
错误数量: {len(mod_errs)}
错误摘要:
{error_messages}

请输出 JSON:
{{
  "pattern": "错误模式总结",
  "suggestions": ["建议1", "建议2", ...],
  "can_auto_fix": true/false,
  "fix_approach": "如可自动修复，描述修复方法"
}}
只输出 JSON，不要其他文字。"""

        try:
            from utils import create_llm_json, extract_json

            llm = create_llm_json(temperature=0.2, max_tokens=1024, timeout=60, max_retries=1)
            response = await llm.ainvoke([{"role": "user", "content": analysis_prompt}])
            raw = response.content if hasattr(response, "content") else str(response)
            analysis = extract_json(raw)
            pattern = analysis.get("pattern", "未识别")
            suggestions = analysis.get("suggestions", [])
            can_fix = analysis.get("can_auto_fix", False)

            fix_suggestions.append(f"[{mod_name}] {pattern}")

            if can_fix and suggestions:
                try:
                    module_output = {
                        "module_name": mod_name,
                        "module_type": "backend",
                        "spec": {"summary": pattern},
                        "code": "",
                        "test_code": "",
                        "error_text": error_messages,
                        "review_issues": suggestions,
                    }
                    repair_result = await _repair_agent.repair(module_output, [])
                    if repair_result.can_fix:
                        for e in mod_errs:
                            await resolve_error(e["trace_id"], resolved_by="batch_analyze",
                                                fix_detail=repair_result.fix_summary)
                        fixes_applied += len(mod_errs)
                        fix_suggestions.append(
                            f"  ✅ 已自动修复: {repair_result.fix_summary}"
                        )
                        try:
                            from workflow.auto_fix import record_fix_case
                            record_fix_case(
                                error_text=error_messages[:200],
                                module_name=mod_name,
                                module_type="backend",
                                fix_summary=repair_result.fix_summary,
                                strategy_used="batch_analyze",
                            )
                        except Exception as exc:
                            logger.warning("批量分析中记录修复案例失败 (%s): %s", mod_name, exc)
                            pass
                except Exception as fix_exc:
                    fix_suggestions.append(f"  ⚠️ 自动修复异常: {fix_exc}")
        except Exception as exc:
            fix_suggestions.append(f"[{mod_name}] 分析失败: {exc}")

    return {
        "status": "completed",
        "errors_analyzed": len(errors),
        "modules_analyzed": analyzed_modules,
        "fixes_applied": fixes_applied,
        "summary": "; ".join(fix_suggestions) if fix_suggestions else "无修复建议",
    }


@app.post("/api/maintenance/clean-logs")
async def clean_logs_endpoint(days: int = 7, dry_run: bool = False) -> dict[str, Any]:
    """清理已解决的旧错误日志。

    POST /api/maintenance/clean-logs?days=7&dry_run=false
    """
    result = await clean_resolved_logs(days=days, dry_run=dry_run)
    return result


@app.get("/api/maintenance/delivery-suggestions")
async def delivery_suggestions_endpoint() -> dict[str, Any]:
    """扫描 deliveries 目录，返回可安全删除的交付物建议。

    GET /api/maintenance/delivery-suggestions
    """
    from workflow.delivery_suggestions import scan_delivery_cleanup_suggestions

    known = await _all_project_ids()
    return scan_delivery_cleanup_suggestions(known)


@app.post("/api/maintenance/delivery-cleanup")
async def delivery_cleanup_endpoint(body: DeliveryCleanupRequest) -> dict[str, Any]:
    """按建议路径删除 deliveries 冗余交付物。

    POST /api/maintenance/delivery-cleanup
    """
    from workflow.delivery_suggestions import apply_delivery_cleanup

    if not body.paths:
        raise HTTPException(400, "请指定要删除的路径")
    known = await _all_project_ids()
    return apply_delivery_cleanup(body.paths, known, dry_run=body.dry_run)


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
    timestamp = datetime.now(timezone.utc).isoformat()

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

    from database.db import async_session_factory

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
        except Exception as exc:
            logger.warning("异常时保存状态失败: %s", exc)
            pass
    finally:
        await asyncio.sleep(5)
        remove_log_queue(project_id)


# ── 入口 ──────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_level="info",
    )
