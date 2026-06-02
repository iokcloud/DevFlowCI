"""项目状态快照 — 供 REST 与 SSE 推送复用。"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select

from database.db import async_session_factory
from database.models import ModuleTask, Project, ProjectLog
from workflow.requirement_context import parse_requirement_addenda


async def fetch_project_snapshot(project_id: str) -> dict[str, Any] | None:
    """从数据库构建与 GET /api/projects/{id} 一致的项目快照。"""
    async with async_session_factory() as db:
        result = await db.execute(
            select(Project).where(Project.project_id == project_id)
        )
        project = result.scalar_one_or_none()
        if not project:
            return None

        mods_result = await db.execute(
            select(ModuleTask)
            .where(ModuleTask.project_id_fk == project.id)
            .order_by(ModuleTask.id)
        )
        modules = mods_result.scalars().all()

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

        return {
            "project_id": project.project_id,
            "display_name": project.display_name or "",
            "requirement": project.requirement,
            "directory": project.directory,
            "status": project.status.value,
            "plan": json.loads(project.plan_json) if project.plan_json else None,
            "alignment": json.loads(project.alignment_json)
            if project.alignment_json
            else None,
            "blocked_count": project.blocked_count,
            "iteration": project.iteration or 1,
            "requirement_addenda": parse_requirement_addenda(
                project.requirement_addendum_json
            ),
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
            "context_scan": json.loads(project.context_scan_json)
            if project.context_scan_json
            else None,
            "created_at": project.created_at.isoformat(),
            "updated_at": project.updated_at.isoformat(),
        }
