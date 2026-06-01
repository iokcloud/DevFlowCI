"""Tests for project delete / cleanup API."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from database.models import Project, ProjectStatus


@pytest.mark.asyncio
async def test_delete_completed_project(memory_db, monkeypatch):
    """DELETE removes a terminal project from DB."""
    from database.db import async_session_factory
    from main import app

    async with async_session_factory() as db:
        p = Project(
            project_id="proj-delete-test01",
            requirement="test req",
            status=ProjectStatus.COMPLETED,
        )
        db.add(p)
        await db.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/projects/proj-delete-test01/delete")
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True

    async with async_session_factory() as db:
        from sqlalchemy import select
        row = await db.execute(
            select(Project).where(Project.project_id == "proj-delete-test01")
        )
        assert row.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_cleanup_batch(memory_db, monkeypatch):
    from database.db import async_session_factory
    from main import app

    async with async_session_factory() as db:
        for i, st in enumerate(
            [ProjectStatus.COMPLETED, ProjectStatus.CANCELLED, ProjectStatus.PLANNING]
        ):
            db.add(
                Project(
                    project_id=f"proj-cleanup-{i:02d}",
                    requirement=f"req {i}",
                    status=st,
                )
            )
        await db.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/projects/cleanup",
            json={
                "statuses": ["completed", "cancelled"],
                "include_stale": False,
                "delete_deliveries": False,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["deleted_count"] == 2
