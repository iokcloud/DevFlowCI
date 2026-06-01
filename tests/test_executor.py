"""Unit tests for workflow.executor helpers and workflow.langgraph_def routing."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select

from database.models import ModuleStatus, ModuleTask, Project, ProjectStatus
from workflow.executor import (
    _detect_document_type,
    _estimate_tokens,
    _persist_plan_modules,
    _sync_project_status,
    get_log_queue,
    push_log,
    remove_log_queue,
)
from workflow.langgraph_def import (
    _after_global_review,
    _after_modules,
    _after_review,
    topological_sort,
)


class TestLogQueue:
    def setup_method(self):
        remove_log_queue("proj-log-test")

    def teardown_method(self):
        remove_log_queue("proj-log-test")

    def test_get_log_queue_creates_queue(self):
        q = get_log_queue("proj-log-test")
        assert q.maxsize == 500

    def test_remove_log_queue(self):
        get_log_queue("proj-log-test")
        remove_log_queue("proj-log-test")
        q2 = get_log_queue("proj-log-test")
        assert q2.empty()

    @pytest.mark.asyncio
    async def test_push_log_enqueues(self):
        await push_log("proj-log-test", "INFO", "hello", module_name="auth")
        q = get_log_queue("proj-log-test")
        entry = q.get_nowait()
        assert entry["level"] == "INFO"
        assert entry["message"] == "hello"
        assert entry["module_name"] == "auth"


class TestHelpers:
    def test_estimate_tokens_mixed(self):
        assert _estimate_tokens("hello world") >= 2
        assert _estimate_tokens("你好世界") == 4

    def test_detect_document_type_business(self):
        text = "市场规模 竞争分析 消费者行为 营收模型 定价策略"
        assert _detect_document_type(text) == "business"

    def test_detect_document_type_technical(self):
        text = "fastapi 后端 api 数据库 微服务 架构 部署 docker"
        assert _detect_document_type(text) == "technical"

    def test_detect_document_type_generic(self):
        assert _detect_document_type("随便一些文字") == "generic"


class TestLanggraphRouting:
    def test_topological_sort(self):
        modules = [
            {"module_name": "db", "dependencies": []},
            {"module_name": "auth", "dependencies": ["db"]},
            {"module_name": "api", "dependencies": ["auth", "db"]},
        ]
        order = topological_sort(modules)
        assert order.index("db") < order.index("auth")
        assert order.index("auth") < order.index("api")

    def test_after_modules_integrate(self):
        state = {"module_results": {"a": {"status": "blocked"}, "b": {"status": "failed"}}}
        assert _after_modules(state) == "integrate"

    def test_after_modules_failed(self):
        state = {"module_results": {"a": {"status": "failed"}}}
        assert _after_modules(state) == "failed"

    def test_after_global_review_package(self):
        assert _after_global_review({"global_review": {"passed": True}}) == "package"
        assert _after_global_review({"global_review": {"passed": False, "score": 60}}) == "package"
        assert _after_global_review({"global_review": {"passed": False, "score": 30}}) == "failed"

    def test_after_review_retry_and_fail(self):
        assert _after_review({"review_result": {"passed": True}}) == "passed"
        assert _after_review({"review_result": {"passed": False}, "retry_count": 1, "max_retries": 5}) == "code"
        assert _after_review({"review_result": {"passed": False}, "retry_count": 5, "max_retries": 5}) == "failed"


class TestExecutorDbSync:
    @pytest.mark.asyncio
    async def test_sync_project_status(self, memory_db):
        async with memory_db() as db:
            db.add(
                Project(
                    project_id="proj-sync",
                    requirement="test",
                    status=ProjectStatus.CREATED,
                )
            )
            await db.commit()

        await _sync_project_status("proj-sync", "planning")
        await asyncio.sleep(0.05)

        async with memory_db() as db:
            result = await db.execute(
                select(Project).where(Project.project_id == "proj-sync")
            )
            project = result.scalar_one()
            assert project.status == ProjectStatus.PLANNING

    @pytest.mark.asyncio
    async def test_persist_plan_modules(self, memory_db):
        async with memory_db() as db:
            db.add(
                Project(
                    project_id="proj-plan",
                    requirement="plan test",
                    status=ProjectStatus.PLANNING,
                )
            )
            await db.commit()

        plan = [
            {"module_name": "auth", "description": "认证", "dependencies": [], "type": "backend"},
            {"module_name": "api", "description": "接口", "dependencies": ["auth"], "type": "backend"},
        ]
        await _persist_plan_modules("proj-plan", '{"modules": []}', plan)

        async with memory_db() as db:
            result = await db.execute(
                select(ModuleTask).where(ModuleTask.module_name == "auth")
            )
            mod = result.scalar_one()
            assert mod.status == ModuleStatus.PENDING
            assert mod.module_type == "backend"
