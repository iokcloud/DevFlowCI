"""历史项目自定义标题。"""

import pytest

from database.models import Project, ProjectStatus


@pytest.mark.asyncio
async def test_update_display_name(memory_db):
    from database.db import async_session_factory
    from main import UpdateDisplayNameRequest, _default_display_name, update_project_display_name

    async with async_session_factory() as db:
        db.add(
            Project(
                project_id="proj-name-test01",
                requirement="用 Python 实现待办 CLI",
                display_name=_default_display_name("用 Python 实现待办 CLI"),
                status=ProjectStatus.COMPLETED,
            )
        )
        await db.commit()

    result = await update_project_display_name(
        "proj-name-test01",
        UpdateDisplayNameRequest(display_name="待办 CLI MVP"),
    )
    assert result["display_name"] == "待办 CLI MVP"

    async with async_session_factory() as db:
        from sqlalchemy import select

        row = (
            await db.execute(
                select(Project).where(Project.project_id == "proj-name-test01")
            )
        ).scalar_one()
        assert row.display_name == "待办 CLI MVP"


def test_default_display_name():
    from main import _default_display_name

    assert _default_display_name("第一行标题\n第二行") == "第一行标题"
    assert _default_display_name("   ") is None
