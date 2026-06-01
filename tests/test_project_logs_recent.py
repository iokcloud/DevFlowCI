"""GET /api/projects/{id}/logs/recent 终态回顾日志。"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from database.models import Project, ProjectLog, ProjectStatus


@pytest.mark.asyncio
async def test_recent_logs_empty_project(memory_db):
    from database.db import async_session_factory
    from main import app

    async with async_session_factory() as db:
        db.add(
            Project(
                project_id="proj-log-empty",
                requirement="test",
                status=ProjectStatus.CANCELLED,
            )
        )
        await db.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/projects/proj-log-empty/logs/recent")
        assert resp.status_code == 200
        assert resp.json() == []


@pytest.mark.asyncio
async def test_recent_logs_returns_persisted(memory_db):
    from database.db import async_session_factory
    from main import app

    async with async_session_factory() as db:
        p = Project(
            project_id="proj-log-one",
            requirement="test",
            status=ProjectStatus.FAILED,
        )
        db.add(p)
        await db.flush()
        db.add(
            ProjectLog(
                project_id_fk=p.id,
                level="WARN",
                message="something broke",
                module_name="auth",
            )
        )
        await db.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/projects/proj-log-one/logs/recent?limit=10")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["level"] == "WARN"
        assert "broke" in data[0]["message"]
