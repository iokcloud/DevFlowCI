"""从数据库重建工作流状态 — 供迭代改进使用。"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select

from database.db import async_session_factory
from database.models import ModuleTask, Project
from workflow.langgraph_def import WorkflowState
from workflow.requirement_context import (
    build_effective_requirement,
    parse_requirement_addenda,
)


def module_task_to_result(mod: ModuleTask) -> dict[str, Any]:
    """ORM 模块记录 → module_results 条目。"""
    spec: dict[str, Any] = {}
    if mod.spec:
        try:
            spec = json.loads(mod.spec)
        except (json.JSONDecodeError, TypeError):
            spec = {}
    return {
        "status": mod.status.value,
        "code": mod.code or "",
        "test_code": mod.tests or "",
        "spec": spec,
        "retry_count": mod.retry_count or 0,
        "failure_reason": mod.failure_reason or "",
    }


async def build_workflow_state_from_db(project_id: str) -> tuple[WorkflowState, Project] | None:
    """从数据库加载项目，重建可继续执行的工作流状态。"""
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
        modules = list(mods_result.scalars().all())

        plan_modules: list[dict[str, Any]] = []
        if project.plan_json:
            try:
                plan_modules = json.loads(project.plan_json).get("modules", [])
            except (json.JSONDecodeError, TypeError):
                plan_modules = []

        if not plan_modules and modules:
            plan_modules = [
                {
                    "module_name": m.module_name,
                    "description": m.description,
                    "dependencies": json.loads(m.dependencies)
                    if m.dependencies
                    else [],
                    "type": m.module_type,
                }
                for m in modules
            ]

        module_results = {
            m.module_name: module_task_to_result(m) for m in modules
        }
        blocked_modules = [
            name
            for name, data in module_results.items()
            if data.get("status") == "blocked"
        ]

        alignment_result: dict[str, Any] = {}
        if project.alignment_json:
            try:
                alignment_result = json.loads(project.alignment_json)
            except (json.JSONDecodeError, TypeError):
                alignment_result = {}

        global_review: dict[str, Any] = {}
        if project.final_report:
            try:
                global_review = json.loads(project.final_report)
            except (json.JSONDecodeError, TypeError):
                global_review = {}

        addenda = parse_requirement_addenda(project.requirement_addendum_json)
        effective_req = build_effective_requirement(project.requirement, addenda)

        state: WorkflowState = {
            "project_id": project_id,
            "requirement": effective_req,
            "base_requirement": project.requirement,
            "requirement_addendum_json": project.requirement_addendum_json,
            "directory": project.directory or "",
            "project_context": "",
            "alignment_result": alignment_result,
            "alternative_alignment": None,
            "plan_json": project.plan_json or "",
            "plan_modules": plan_modules,
            "module_results": module_results,
            "module_order": [],
            "blocked_modules": blocked_modules,
            "integration_result": {},
            "global_review": global_review,
            "delivery_path": project.delivery_path or "",
            "status": project.status.value,
            "errors": [],
            "iteration": project.iteration or 1,
        }

        mvp_max = alignment_result.get("mvp_max_modules")
        if mvp_max is not None:
            state["mvp_max_modules"] = mvp_max

        return state, project
