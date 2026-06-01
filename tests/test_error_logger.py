"""Unit tests for workflow.error_logger."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from database.models import ErrorLog, ErrorStatus
from workflow.error_logger import (
    clean_resolved_logs,
    get_error_stats,
    get_open_errors,
    resolve_error,
    write_error_log,
)


@pytest.mark.asyncio
async def test_write_and_get_open_errors(memory_db, tmp_path, monkeypatch):
    log_file = tmp_path / "server.log"
    monkeypatch.setattr("workflow.error_logger.ERROR_LOG_FILE", log_file)

    db_id = await write_error_log(
        "proj-001",
        "模块 auth 审查失败",
        module_name="auth",
        error_type="review_fail",
        trace_id="trace-test-001",
    )
    assert db_id is not None
    assert log_file.exists()
    assert "review_fail" in log_file.read_text(encoding="utf-8")

    open_errors = await get_open_errors(project_id="proj-001")
    assert len(open_errors) == 1
    assert open_errors[0]["trace_id"] == "trace-test-001"
    assert open_errors[0]["module_name"] == "auth"


@pytest.mark.asyncio
async def test_resolve_error(memory_db, tmp_path, monkeypatch):
    monkeypatch.setattr("workflow.error_logger.ERROR_LOG_FILE", tmp_path / "server.log")

    await write_error_log(
        "proj-002",
        "timeout error",
        trace_id="trace-resolve",
        error_type="api_timeout",
    )

    ok = await resolve_error("trace-resolve", resolved_by="auto_fix")
    assert ok is True

    open_errors = await get_open_errors(project_id="proj-002")
    assert open_errors == []

    stats = await get_error_stats(project_id="proj-002")
    assert stats["total"] == 1
    assert stats["open"] == 0
    assert stats["resolved"] == 1


@pytest.mark.asyncio
async def test_get_error_stats_by_module_and_type(memory_db, tmp_path, monkeypatch):
    monkeypatch.setattr("workflow.error_logger.ERROR_LOG_FILE", tmp_path / "server.log")

    await write_error_log("proj-003", "err1", module_name="auth", error_type="syntax_error", trace_id="t1")
    await write_error_log("proj-003", "err2", module_name="auth", error_type="test_failure", trace_id="t2")
    await write_error_log("proj-003", "err3", module_name="payment", error_type="syntax_error", trace_id="t3")

    stats = await get_error_stats(project_id="proj-003")
    assert stats["total"] == 3
    assert stats["open"] == 3
    assert len(stats["by_module"]) >= 2
    assert len(stats["by_type"]) >= 2
    assert len(stats["recent"]) == 3


@pytest.mark.asyncio
async def test_clean_resolved_logs(memory_db, tmp_path, monkeypatch):
    monkeypatch.setattr("workflow.error_logger.ERROR_LOG_FILE", tmp_path / "server.log")

    await write_error_log("proj-004", "old resolved", trace_id="old-1")
    await resolve_error("old-1")

    async with memory_db() as db:
        result = await db.execute(select(ErrorLog).where(ErrorLog.trace_id == "old-1"))
        row = result.scalar_one()
        row.created_at = datetime.now(timezone.utc) - timedelta(days=10)
        await db.commit()

    dry = await clean_resolved_logs(days=7, dry_run=True)
    assert dry["deleted_count"] == 1
    assert dry["dry_run"] is True

    deleted = await clean_resolved_logs(days=7, dry_run=False)
    assert deleted["deleted_count"] == 1

    stats = await get_error_stats(project_id="proj-004")
    assert stats["total"] == 0
