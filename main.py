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
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
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
    DELIVERIES_DIR,
    MEMORY_DIR,
    STATIC_DIR,
    MAX_HUMAN_FIXES,
    PROJECT_ROOT,
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    DEEPSEEK_MODEL,
    BUSINESS_MVP_MAX_MODULES,
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
    write_error_log,
    resolve_error,
    get_error_stats,
    get_open_errors,
    clean_resolved_logs,
)
from workflow.executor import (
    WorkflowExecutor,
    WorkflowState,
    push_log,
    remove_log_queue,
    stream_logs,
)
from workflow.stream_relay import (
    stream_ai_tokens,
    stream_deepseek_call,
    push_ai_token,
    remove_stream_queue,
    get_stream_queue,
)

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


# ── 请求模型 ──────────────────────────────────────────────

class CreateProjectRequest(BaseModel):
    """创建项目请求体。"""
    requirement: str = Field(default="", description="用户自然语言需求")
    directory: str | None = Field(default=None, description="项目目录绝对路径（选填）")
    mode: str = Field(default="auto", description="计划类型: auto/technical/business")
    force_new: bool = Field(default=False, description="强制创建新项目，跳过去重（重试场景使用）")


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


# ── API 路由 ──────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    """前端首页。"""
    html_path = STATIC_DIR / "index.html"
    if html_path.exists():
        return HTMLResponse(html_path.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>DevFlow CI</h1><p>前端文件未找到。</p>")


@app.post("/api/projects")
async def create_project(
    body: CreateProjectRequest,
) -> dict[str, Any]:
    """创建新项目并启动后台工作流。

    POST /api/projects
    Body: {requirement: "...", directory: "D:/path/to/project" | null}

    交互规则：
    - 目录为空 + 需求为空 → 前端阻止（后端返回 400）
    - 目录为空 + 需求非空 → 从零开始工作流
    - 目录非空 + 需求为空 → 自动生成分析需求
    - 目录非空 + 需求非空 → 增量开发
    """
    from database.db import async_session_factory

    requirement = body.requirement.strip()
    directory = body.directory.strip() if body.directory else ""

    # 验证：目录和需求至少有一项
    if not requirement and not directory:
        raise HTTPException(400, "请输入需求或选择已有项目目录")

    # 目录非空 + 需求为空 → 自动生成需求
    if not requirement and directory:
        if not os.path.isdir(directory):
            raise HTTPException(400, f"项目目录不存在或无效: {directory}")
        requirement = "请分析现有项目代码，找出可优化、修复或完善的地方，并执行相应开发"

    # 验证目录有效性
    if directory and not os.path.isdir(directory):
        raise HTTPException(400, f"项目目录不存在或无效: {directory}")

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
            requirement=requirement,
            directory=directory if directory else None,
            status=ProjectStatus.CREATED,
        )
        db.add(project)
        await db.commit()

    # 后台启动统一工作流
    state: WorkflowState = {
        "project_id": project_id,
        "requirement": requirement,
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
    from database.db import async_session_factory
    from sqlalchemy import desc, select

    async with async_session_factory() as db:
        result = await db.execute(
            select(Project)
            .order_by(desc(Project.created_at))
            .limit(limit)
        )
        projects = result.scalars().all()

        return [
            {
                "project_id": p.project_id,
                "requirement": p.requirement[:100] + "..."
                if len(p.requirement) > 100
                else p.requirement,
                "status": p.status.value,
                "directory": p.directory,
                "blocked_count": p.blocked_count,
                "created_at": p.created_at.isoformat(),
            }
            for p in projects
        ]


@app.get("/api/projects/{project_id}")
async def get_project(project_id: str) -> dict[str, Any]:
    """获取项目完整状态。

    GET /api/projects/{project_id}
    """
    from database.db import async_session_factory

    async with async_session_factory() as db:
        from sqlalchemy import select
        result = await db.execute(
            select(Project).where(Project.project_id == project_id)
        )
        project = result.scalar_one_or_none()
        if not project:
            raise HTTPException(404, "项目不存在")

        # 查询模块
        mods_result = await db.execute(
            select(ModuleTask)
            .where(ModuleTask.project_id_fk == project.id)
            .order_by(ModuleTask.id)
        )
        modules = mods_result.scalars().all()

        # 查询最近 ERROR/WARN 日志（用于异常面板展示）
        logs_result = await db.execute(
            select(ProjectLog)
            .where(
                ProjectLog.project_id_fk == project.id,
                ProjectLog.level.in_(["ERROR", "WARN"]),
            )
            .order_by(ProjectLog.timestamp.desc())
            .limit(25)
        )
        recent_error_logs = [
            {
                "level": log.level,
                "message": log.message,
                "module_name": log.module_name,
                "timestamp": log.timestamp.isoformat(),
            }
            for log in reversed(logs_result.scalars().all())
        ]

        # 构建响应
        response: dict[str, Any] = {
            "project_id": project.project_id,
            "requirement": project.requirement,
            "directory": project.directory,
            "status": project.status.value,
            "plan": json.loads(project.plan_json)
            if project.plan_json
            else None,
            "alignment": json.loads(project.alignment_json)
            if project.alignment_json
            else None,
            "blocked_count": project.blocked_count,
            "recent_error_logs": recent_error_logs,
            "modules": [
                {
                    "module_name": m.module_name,
                    "description": m.description,
                    "dependencies": json.loads(m.dependencies)
                    if m.dependencies
                    else [],
                    "type": m.module_type,
                    "status": m.status.value,
                    "spec": m.spec,
                    "code": m.code,
                    "tests": m.tests,
                    "review_result": m.review_result,
                    "retry_count": m.retry_count,
                    "failure_reason": m.failure_reason,
                    "auto_fix_history": json.loads(m.auto_fix_history)
                    if m.auto_fix_history
                    else [],
                    "test_result": json.loads(m.test_result)
                    if m.test_result
                    else None,
                }
                for m in modules
            ],
            "final_report": project.final_report,
            "delivery_path": project.delivery_path,
            "test_report_path": project.test_report_path,
            "created_at": project.created_at.isoformat(),
            "updated_at": project.updated_at.isoformat(),
        }
        return response


@app.get("/api/projects/{project_id}/logs")
async def get_logs_sse(project_id: str) -> StreamingResponse:
    """实时日志流（SSE）。

    GET /api/projects/{project_id}/logs
    """
    from database.db import async_session_factory
    from sqlalchemy import select

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
    from database.db import async_session_factory
    from sqlalchemy import select

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
    from database.db import async_session_factory
    from sqlalchemy import select

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
            mvp_max_modules = BUSINESS_MVP_MAX_MODULES
            if alignment_result.get("plan_type") == "business":
                original_requirement = project.requirement
                tech_requirement, mvp_max_modules = _build_business_tech_requirement(
                    original_requirement, alignment_result
                )
                project.requirement = tech_requirement[:2000]
                await push_log(
                    project_id, "INFO",
                    f"商业计划已确认，提取技术需求（MVP≤{mvp_max_modules}模块）："
                    f"{tech_requirement[:200]}...",
                )

            # 将最终对齐计划写入 DELIVERY_PLAN.md
            _write_delivery_plan(project_id, project)

            # 用户选方案
            plan_choice = body.plan_choice or "A"
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
                "mvp_max_modules": mvp_max_modules,
            }

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

    # 启动执行
    state: WorkflowState = {
        "project_id": project_id,
        "requirement": project.requirement,
        "directory": project.directory or "",
        "project_context": "",
        "alignment_result": json.loads(project.alignment_json) if project.alignment_json else {},
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

    _register_task(project_id, _run_execution(project_id, state))

    return {"status": "executing"}


@app.post("/api/projects/{project_id}/cancel")
async def cancel_project(project_id: str) -> dict[str, Any]:
    """终止正在运行的项目。

    POST /api/projects/{project_id}/cancel
    """
    from database.db import async_session_factory
    from sqlalchemy import select

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
            project.status = ProjectStatus.CANCELLED
            await db.commit()

    return {"status": "cancelled", "project_id": project_id}


@app.get("/api/projects/{project_id}/download")
async def download_project(project_id: str) -> FileResponse:
    """下载项目 ZIP 包。

    GET /api/projects/{project_id}/download
    """
    zip_path = DELIVERIES_DIR / f"{project_id}.zip"
    if not zip_path.exists():
        raise HTTPException(404, "交付物尚未生成")

    return FileResponse(
        path=str(zip_path),
        filename=f"{project_id}.zip",
        media_type="application/zip",
    )


@app.get("/api/health")
async def health() -> dict[str, str]:
    """健康检查。"""
    return {"status": "ok", "cases_count": str(_case_store.count)}


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
    except PermissionError:
        raise HTTPException(403, f"没有权限访问: {path}")


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
    except Exception:
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
            from langchain_openai import ChatOpenAI
            llm = ChatOpenAI(
                model=DEEPSEEK_MODEL,
                api_key=DEEPSEEK_API_KEY,
                base_url=DEEPSEEK_BASE_URL,
                temperature=0.2,
                max_tokens=1024,
                timeout=60,
                max_retries=1,
            )
            response = await llm.ainvoke([{"role": "user", "content": analysis_prompt}])
            raw = response.content if hasattr(response, "content") else str(response)
            import re as _re
            json_match = _re.search(r"\{[\s\S]*\}", raw)
            if json_match:
                analysis = json.loads(json_match.group())
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
                            except Exception:
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


# ── 后台工作流执行 ────────────────────────────────────────

def _build_business_tech_requirement(
    original_requirement: str,
    alignment_result: dict[str, Any],
) -> tuple[str, int]:
    """商业计划确认后生成技术需求与 MVP 模块上限。

    若原始需求含 is_prime/素数 等单函数 MVP 提示，走与技术冒烟相同的单模块路径。
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

    # 纯商业：Phase 1 单模块 MVP（避免 PM 拆成多个抽象模块后全部 blocked）
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
    except Exception:
        pass


def _write_delivery_plan(project_id: str, project: Any) -> None:
    """将用户确认后的最终执行计划写入 DELIVERY_PLAN.md。"""
    from config import DELIVERIES_DIR as deliveries_dir

    plan_dir = deliveries_dir / project_id
    plan_dir.mkdir(parents=True, exist_ok=True)

    lines = [
        "# 执行计划 (DELIVERY PLAN)",
        "",
        f"> 项目: {project_id}",
        f"> 确认时间: {datetime.now(timezone.utc).isoformat()}",
        "",
    ]

    # 写入对齐分析结果
    if project.alignment_json:
        try:
            alignment = json.loads(project.alignment_json)
            lines.append(f"## 摘要")
            lines.append(f"{alignment.get('summary', 'N/A')}")
            lines.append("")

            assumptions = alignment.get("assumptions", [])
            if assumptions:
                lines.append("## 假设")
                for a in assumptions:
                    lines.append(f"- {a}")
                lines.append("")

            risks = alignment.get("risks", [])
            if risks:
                lines.append("## 风险")
                for r in risks:
                    lines.append(f"- {r}")
                lines.append("")

            plan = alignment.get("plan", [])
            if plan:
                lines.append("## 计划模块")
                lines.append("")
                for i, m in enumerate(plan, 1):
                    lines.append(f"### {i}. {m.get('module', '未命名')}")
                    lines.append(f"- **描述**: {m.get('description', 'N/A')}")
                    lines.append(f"- **原因**: {m.get('reason', 'N/A')}")
                    lines.append(f"- **类型**: {m.get('type', 'backend')}")
                    lines.append("")
        except Exception:
            lines.append("（对齐数据解析失败）")
            lines.append("")

    plan_path = plan_dir / "DELIVERY_PLAN.md"
    try:
        plan_path.write_text("\n".join(lines), encoding="utf-8")
    except Exception:
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
        except Exception:
            pass
    finally:
        await asyncio.sleep(5)
        remove_log_queue(project_id)


async def _save_state(project_id: str, final_state: WorkflowState) -> None:
    """持久化工作流状态到数据库。"""
    from database.db import async_session_factory
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

        review = final_state.get("global_review", {})
        if review:
            project.final_report = json.dumps(review, ensure_ascii=False)
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
        except Exception:
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


# ── 入口 ──────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info",
    )
