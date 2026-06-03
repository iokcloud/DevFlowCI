"""项目 CRUD 端点。"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta, timezone

UTC = timezone.utc
from typing import Any

from fastapi import APIRouter, HTTPException

from api.models import (
    _ACTIVE_PROJECT_STATUSES,
    _TERMINAL_PROJECT_STATUSES,
    CleanupProjectsRequest,
    CreateProjectRequest,
    UpdateDisplayNameRequest,
)
from database.db import async_session_factory
from database.models import Project, ProjectLog, ProjectStatus
from workflow.langgraph_def import WorkflowState
from workflow.sse_bridge import push_log, remove_log_queue
from workflow.workflow_runner import _register_task, _run_workflow, _running_tasks

router = APIRouter(prefix="/api/projects", tags=["projects"])

async def _delete_project_record(
    project_id: str,
    *,
    delete_deliveries: bool = True,
) -> bool:
    """删除单个项目记录及关联交付物/日志。返回是否删除成功。"""
    from sqlalchemy import delete, select

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

    from database.models import Project

    async with async_session_factory() as db:
        result = await db.execute(select(Project.project_id))
        return {row[0] for row in result.all()}


@router.post("")
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
                if dup.updated_at and (datetime.now(UTC) - dup.updated_at) > _zombie_timeout:
                    should_replace = True
                    elapsed = (datetime.now(UTC) - dup.updated_at).seconds // 60
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


@router.get("/history")
async def get_history(limit: int = 20) -> list[dict[str, Any]]:
    """获取历史项目列表。

    GET /api/projects/history?limit=20
    """
    from sqlalchemy import desc, select

    limit = max(1, min(limit, 100))  # 防止超大请求压垮数据库
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
    cutoff = datetime.now(UTC) - timedelta(minutes=10)
    updated = project.updated_at
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=UTC)
    return updated < cutoff


async def _delete_project_impl(
    project_id: str,
    *,
    delete_deliveries: bool = True,
) -> dict[str, Any]:
    """删除历史项目（运行中项目会先终止）。"""
    from sqlalchemy import select


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


@router.delete("/{project_id}")
async def delete_project(
    project_id: str,
    delete_deliveries: bool = True,
) -> dict[str, Any]:
    """DELETE /api/projects/{project_id}?delete_deliveries=true"""
    return await _delete_project_impl(
        project_id, delete_deliveries=delete_deliveries
    )


@router.post("/{project_id}/delete")
async def delete_project_post(
    project_id: str,
    delete_deliveries: bool = True,
) -> dict[str, Any]:
    """POST /api/projects/{project_id}/delete?delete_deliveries=true（与 DELETE 等效）"""
    return await _delete_project_impl(
        project_id, delete_deliveries=delete_deliveries
    )


@router.post("/cleanup")
async def cleanup_projects(body: CleanupProjectsRequest) -> dict[str, Any]:
    """批量清理历史项目。

    POST /api/projects/cleanup
    """
    from sqlalchemy import select


    allowed = {s.value for s in ProjectStatus}
    target_statuses = {s for s in body.statuses if s in allowed}
    if not target_statuses and not body.include_stale:
        raise HTTPException(400, "请指定要清理的状态或启用 include_stale")

    stale_cutoff = datetime.now(UTC) - timedelta(minutes=body.stale_minutes)
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
                updated = updated.replace(tzinfo=UTC)
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


@router.get("/{project_id}")
async def get_project(project_id: str) -> dict[str, Any]:
    """获取项目完整状态。

    GET /api/projects/{project_id}
    """
    from workflow.project_snapshot import fetch_project_snapshot

    snapshot = await fetch_project_snapshot(project_id)
    if not snapshot:
        raise HTTPException(404, "项目不存在")
    return snapshot


@router.patch("/{project_id}/display_name")
async def update_project_display_name(
    project_id: str,
    body: UpdateDisplayNameRequest,
) -> dict[str, Any]:
    """更新历史项目自定义标题。

    PATCH /api/projects/{project_id}/display_name
    """
    from sqlalchemy import select


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



@router.get("/{project_id}/logs/recent")
async def get_recent_logs(project_id: str, limit: int = 150) -> list[dict[str, Any]]:
    """获取项目历史日志（非 SSE，用于终态回顾）。

    GET /api/projects/{project_id}/logs/recent?limit=150
    """
    from sqlalchemy import desc, select


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



