"""Shared pytest fixtures."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from database.models import Base


@pytest.fixture
def tmp_auto_fix_cases(tmp_path: Path, monkeypatch):
    """Isolated auto_fix_cases.json for workflow.auto_fix tests."""
    cases_file = tmp_path / "auto_fix_cases.json"
    monkeypatch.setattr("workflow.auto_fix.AUTO_FIX_CASES_FILE", cases_file)
    return cases_file


@pytest.fixture
def tmp_success_cases(tmp_path: Path, monkeypatch):
    """Isolated success_cases.json for memory.case_store tests."""
    cases_file = tmp_path / "success_cases.json"
    monkeypatch.setattr("memory.case_store.SUCCESS_CASES_FILE", cases_file)
    return cases_file


@pytest.fixture
async def db_session():
    """In-memory SQLite session for database.models tests."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    async with factory() as session:
        yield session

    await engine.dispose()


@pytest.fixture
async def memory_db(monkeypatch):
    """Patch database.db.async_session_factory to in-memory SQLite."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    monkeypatch.setattr("database.db.async_session_factory", factory)
    yield factory
    await engine.dispose()


def write_json_cases(path: Path, cases: list[dict]) -> None:
    """Helper: write case list to JSON file."""
    path.write_text(
        json.dumps({"version": "1.0.0", "cases": cases}, ensure_ascii=False),
        encoding="utf-8",
    )
