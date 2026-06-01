"""WorkflowExecutor integration tests with mocked LLM agents."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from agents.integrator import GlobalReviewResult, IntegrationResult
from agents.module_agents import ModuleCode, ModuleSpec, ModuleTestResult
from agents.reviewer import ReviewResult
from database.models import Project, ProjectStatus
from workflow.executor import WorkflowExecutor, remove_log_queue
from workflow.langgraph_def import WorkflowState

pytestmark = pytest.mark.integration


@pytest.fixture
def project_id() -> str:
    return "proj-integration-test"


@pytest.fixture
async def seeded_project(memory_db, project_id: str):
    async with memory_db() as db:
        db.add(
            Project(
                project_id=project_id,
                requirement="integration test requirement",
                status=ProjectStatus.CREATED,
            )
        )
        await db.commit()


@pytest.fixture
def sample_plan_module() -> dict:
    return {
        "module_name": "hello",
        "description": "返回 hello 的简单 API",
        "dependencies": [],
        "type": "backend",
    }


@pytest.fixture
def mock_agents(sample_plan_module: dict):
    spec = ModuleSpec(
        module_name=sample_plan_module["module_name"],
        summary="hello API",
        api_endpoints=["GET /hello"],
    )
    code = ModuleCode(
        module_name=sample_plan_module["module_name"],
        code="def hello():\n    return 'hello'\n",
        test_code="def test_hello():\n    assert hello() == 'hello'\n",
    )
    test_result = ModuleTestResult(
        module_name=sample_plan_module["module_name"],
        passed=True,
        details="mock ok",
    )
    review = ReviewResult(passed=True, summary="mock review passed")

    planner = MagicMock()
    planner.plan_alternatives = AsyncMock(
        return_value=(
            {
                "modules": [sample_plan_module],
                "global_requirements": ["使用 FastAPI"],
            },
            {},
            {},
            [],
        )
    )
    planner.infer_dependencies = AsyncMock(
        return_value={"dependencies": [], "system_dependencies": [], "summary": "mock"}
    )

    alignment_agent = MagicMock()
    alignment_agent.analyze_alternatives = AsyncMock(
        return_value=(
            {
                "summary": "对齐完成",
                "plan": [
                    {
                        "module": "hello",
                        "description": "hello API",
                        "type": "backend",
                    }
                ],
                "assumptions": [],
                "risks": [],
                "questions": [],
            },
            {},
            [],
        )
    )

    modules = MagicMock()
    modules.analyze = AsyncMock(return_value=spec)
    modules.code = AsyncMock(return_value=code)
    modules.test = AsyncMock(return_value=test_result)

    reviewer = MagicMock()
    reviewer.review = AsyncMock(return_value=review)

    integrator = MagicMock()
    integrator.integrate = AsyncMock(
        return_value=IntegrationResult(
            project_structure='{"hello.py": "main module"}',
            main_code="from hello import hello\n",
            integration_tests="def test_integration():\n    assert True\n",
            readme="# Hello Project\n",
            requirements="fastapi>=0.115.0\n",
        )
    )

    global_reviewer = MagicMock()
    global_reviewer.review = AsyncMock(
        return_value=GlobalReviewResult(
            passed=True,
            score=92,
            summary="集成良好",
        )
    )

    repair_agent = MagicMock()
    business_planner = MagicMock()

    return {
        "planner": planner,
        "modules": modules,
        "reviewer": reviewer,
        "integrator": integrator,
        "global_reviewer": global_reviewer,
        "repair_agent": repair_agent,
        "alignment_agent": alignment_agent,
        "business_planner": business_planner,
    }


@pytest.fixture
def executor(mock_agents, tmp_success_cases: Path):
    return WorkflowExecutor(
        planner=mock_agents["planner"],
        modules=mock_agents["modules"],
        reviewer=mock_agents["reviewer"],
        integrator=mock_agents["integrator"],
        global_reviewer=mock_agents["global_reviewer"],
        repair_agent=mock_agents["repair_agent"],
        alignment_agent=mock_agents["alignment_agent"],
        business_planner=mock_agents["business_planner"],
    )


@pytest.fixture
def base_state(project_id: str) -> WorkflowState:
    return {
        "project_id": project_id,
        "requirement": "实现一个简单的 hello 模块 API",
        "directory": "",
        "module_results": {},
        "errors": [],
        "blocked_modules": [],
    }


@pytest.fixture(autouse=True)
def cleanup_log_queue(project_id: str):
    yield
    remove_log_queue(project_id)


@pytest.mark.asyncio
async def test_execute_stops_at_aligned(executor, base_state, seeded_project):
    result = await executor.execute(base_state)

    assert result["status"] == "aligned"
    assert "alignment_result" in result
    assert result["alignment_result"]["summary"] == "对齐完成"
    executor._alignment_agent.analyze_alternatives.assert_awaited()


@pytest.mark.asyncio
async def test_plan_only_reaches_plan_ready(
    executor, base_state, seeded_project, sample_plan_module
):
    result = await executor.plan_only(base_state)

    assert result["status"] == "plan_ready"
    assert len(result["plan_modules"]) == 1
    assert result["plan_modules"][0]["module_name"] == sample_plan_module["module_name"]
    executor._planner.plan_alternatives.assert_awaited()


@pytest.mark.asyncio
async def test_execute_after_alignment_pipeline(executor, base_state, seeded_project):
    result = await executor.execute_after_alignment(base_state)

    assert result["status"] == "plan_ready"
    assert result.get("plan_json")
    executor._planner.plan_alternatives.assert_awaited()


@pytest.mark.asyncio
async def test_execute_from_plan_completes_with_mocks(
    executor,
    base_state,
    seeded_project,
    sample_plan_module,
    tmp_path: Path,
    monkeypatch,
):
    monkeypatch.setattr("workflow.executor.TEST_EXECUTION_ENABLED", False)
    monkeypatch.setattr("workflow.executor.AUTOPILOT_ENABLED", False)
    monkeypatch.setattr("workflow.executor.DELIVERIES_DIR", tmp_path / "deliveries")

    base_state["plan_modules"] = [sample_plan_module]
    base_state["plan_json"] = '{"modules": []}'
    base_state["status"] = "plan_ready"

    result = await executor.execute_from_plan(base_state)

    assert result["status"] == "completed"
    assert "hello" in result["module_results"]
    assert result["module_results"]["hello"]["status"] == "passed"
    assert result.get("delivery_path")
    assert Path(result["delivery_path"]).exists()
    executor._modules.analyze.assert_awaited()
    executor._integrator.integrate.assert_awaited()
    executor._global_reviewer.review.assert_awaited()


@pytest.mark.asyncio
async def test_execute_from_plan_module_review_fail_then_blocked(
    executor,
    base_state,
    seeded_project,
    sample_plan_module,
    monkeypatch,
):
    """审查始终失败且无有效代码时，模块应进入 blocked 而非卡死。"""
    monkeypatch.setattr("workflow.executor.TEST_EXECUTION_ENABLED", False)
    monkeypatch.setattr("workflow.executor.AUTO_FIX_ENABLED", False)
    monkeypatch.setattr("workflow.executor.MAX_REVIEW_RETRIES", 1)

    fail_review = ReviewResult(passed=False, summary="fail", issues=["缺少错误处理"])
    executor._reviewer.review = AsyncMock(return_value=fail_review)

    base_state["plan_modules"] = [sample_plan_module]
    base_state["plan_json"] = "{}"

    result = await executor.execute_from_plan(base_state)

    mod = result["module_results"].get("hello", {})
    assert mod.get("status") in ("blocked", "failed")
    assert result["status"] in ("failed", "needs_review", "completed", "completed_with_warnings")
