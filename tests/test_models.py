"""Unit tests for database.models."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from database.models import (
    ErrorLog,
    ErrorStatus,
    FixSession,
    ModuleStatus,
    ModuleTask,
    Project,
    ProjectLog,
    ProjectStatus,
)


@pytest.mark.asyncio
async def test_create_project(db_session):
    project = Project(
        project_id="proj-test-001",
        requirement="测试需求",
        status=ProjectStatus.CREATED,
    )
    db_session.add(project)
    await db_session.commit()
    await db_session.refresh(project)

    assert project.id is not None
    assert project.status == ProjectStatus.CREATED
    assert project.blocked_count == 0


@pytest.mark.asyncio
async def test_project_module_relationship(db_session):
    project = Project(
        project_id="proj-test-002",
        requirement="多模块项目",
        status=ProjectStatus.EXECUTING,
    )
    module = ModuleTask(
        module_name="auth",
        description="认证模块",
        module_type="backend",
        status=ModuleStatus.PENDING,
        dependencies="[]",
    )
    project.modules.append(module)
    db_session.add(project)
    await db_session.commit()

    result = await db_session.execute(
        select(ModuleTask).where(ModuleTask.module_name == "auth")
    )
    loaded = result.scalar_one()
    assert loaded.project.project_id == "proj-test-002"
    assert loaded.status == ModuleStatus.PENDING


@pytest.mark.asyncio
async def test_project_log_cascade(db_session):
    project = Project(
        project_id="proj-test-003",
        requirement="日志测试",
        status=ProjectStatus.PLANNING,
    )
    project.logs.append(
        ProjectLog(level="INFO", message="开始规划", module_name=None)
    )
    db_session.add(project)
    await db_session.commit()

    pid = project.id
    await db_session.delete(project)
    await db_session.commit()

    remaining = await db_session.execute(
        select(ProjectLog).where(ProjectLog.project_id_fk == pid)
    )
    assert remaining.scalars().all() == []


@pytest.mark.asyncio
async def test_error_log_defaults(db_session):
    err = ErrorLog(
        trace_id="trace-abc",
        message="测试异常",
    )
    db_session.add(err)
    await db_session.commit()
    await db_session.refresh(err)

    assert err.status == ErrorStatus.OPEN
    assert err.id is not None


@pytest.mark.asyncio
async def test_fix_session_fields(db_session):
    project = Project(
        project_id="proj-test-004",
        requirement="修复会话",
        status=ProjectStatus.EXECUTING,
    )
    db_session.add(project)
    await db_session.flush()

    session = FixSession(
        project_id_fk=project.id,
        project_id="proj-test-004",
        module_name="payment",
        strategy_used="modify_code",
        round_number=1,
        success=1,
        fix_summary="修复导入错误",
    )
    db_session.add(session)
    await db_session.commit()
    await db_session.refresh(session)

    assert session.module_name == "payment"
    assert session.success == 1


def test_enum_string_values():
    assert ProjectStatus.ALIGNED.value == "aligned"
    assert ModuleStatus.AUTO_FIXING.value == "auto_fixing"
    assert ErrorStatus.RESOLVED.value == "resolved"
