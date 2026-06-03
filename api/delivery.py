"""交付与版本管理端点。"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select as _sel

from api.models import RollbackRequest
from config import DELIVERIES_DIR
from database.db import async_session_factory
from database.models import Project, ProjectStatus
from workflow.sse_bridge import push_log
from workflow.workflow_runner import _build_business_tech_requirement, _running_tasks

router = APIRouter(prefix="/api/projects", tags=["delivery"])

@router.get("/{project_id}/business_tech_preview")
async def business_tech_preview(project_id: str) -> dict[str, Any]:
    """商业计划确认前预览：将生成的技术需求与预计模块数。

    GET /api/projects/{project_id}/business_tech_preview
    """
    from sqlalchemy import select


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



@router.get("/{project_id}/download")
async def download_project(project_id: str) -> FileResponse:
    """下载项目 ZIP 包。

    GET /api/projects/{project_id}/download
    """
    from sqlalchemy import select

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



# ── 版本管理 API ────────────────────────────────────────

@router.get("/{project_id}/versions")
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


@router.post("/{project_id}/rollback")
async def rollback_version(project_id: str, body: RollbackRequest) -> dict[str, Any]:
    """回滚到指定版本。"""
    base_dir = DELIVERIES_DIR / project_id
    target = base_dir / body.version
    if not target.exists():
        raise HTTPException(404, f"版本 {body.version} 不存在")
    # 复制最新版本
    latest_num = max([int(d.name[1:]) for d in base_dir.iterdir() if d.is_dir() and d.name.startswith("v") and d.name[1:].isdigit()] or [0])
    new_ver = base_dir / f"v{latest_num + 1}"
    shutil.copytree(target, new_ver)
    await push_log(project_id, "INFO", f"从 {body.version} 回滚，创建新版本 v{latest_num + 1}")
    return {"status": "rolled_back", "from": body.version, "to": f"v{latest_num + 1}"}


# ── 反馈学习 API ────────────────────────────────────────

