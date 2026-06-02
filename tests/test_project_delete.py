"""Tests for project delete / cleanup API and artifact removal."""

from __future__ import annotations

import json

import pytest
from httpx import ASGITransport, AsyncClient

from database.models import ErrorLog, ErrorStatus, Project, ProjectStatus
from workflow.project_cleanup import cleanup_delivery_artifacts, prune_human_fixes


def test_cleanup_delivery_artifacts(tmp_path, monkeypatch):
    monkeypatch.setattr("workflow.project_cleanup.DELIVERIES_DIR", tmp_path)
    pid = "proj-cleanup-disk"
    (tmp_path / pid).mkdir()
    (tmp_path / f"{pid}.zip").write_bytes(b"zip")

    result = cleanup_delivery_artifacts(pid)

    assert result["delivery_dir"] is True
    assert result["delivery_zip"] is True
    assert not (tmp_path / pid).exists()
    assert not (tmp_path / f"{pid}.zip").exists()


def test_prune_human_fixes(tmp_path, monkeypatch):
    monkeypatch.setattr("workflow.project_cleanup.MEMORY_DIR", tmp_path)
    fixes_file = tmp_path / "human_fixes.json"
    fixes_file.write_text(
        json.dumps(
            {
                "fixes": [
                    {"project_id": "a", "fix_type": "x"},
                    {"project_id": "b", "fix_type": "y"},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    removed = prune_human_fixes("a")
    assert removed == 1
    data = json.loads(fixes_file.read_text(encoding="utf-8"))
    assert len(data["fixes"]) == 1
    assert data["fixes"][0]["project_id"] == "b"


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
async def test_delete_removes_deliveries_and_error_logs(memory_db, monkeypatch, tmp_path):
    from database.db import async_session_factory
    from main import app

    monkeypatch.setattr("main.DELIVERIES_DIR", tmp_path)
    monkeypatch.setattr("workflow.project_cleanup.DELIVERIES_DIR", tmp_path)

    pid = "proj-delete-full"
    (tmp_path / pid).mkdir()
    (tmp_path / f"{pid}.zip").write_bytes(b"zip")

    async with async_session_factory() as db:
        db.add(
            Project(
                project_id=pid,
                requirement="req",
                status=ProjectStatus.COMPLETED,
            )
        )
        db.add(
            ErrorLog(
                trace_id="t-1",
                project_id=pid,
                message="boom",
                status=ErrorStatus.OPEN,
            )
        )
        await db.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(f"/api/projects/{pid}/delete")
        assert resp.status_code == 200

    assert not (tmp_path / pid).exists()
    assert not (tmp_path / f"{pid}.zip").exists()

    async with async_session_factory() as db:
        from sqlalchemy import select
        err = await db.execute(select(ErrorLog).where(ErrorLog.project_id == pid))
        assert err.scalar_one_or_none() is None


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
                "delete_deliveries": True,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["deleted_count"] == 2
