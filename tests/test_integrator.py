"""Tests for integrator global reviewer string handling."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from agents.integrator import GlobalReviewResult, GlobalReviewerAgent, IntegrationResult


def test_integration_result_accepts_dict_structure():
    result = IntegrationResult(
        project_structure={"app.py": "main", "auth.py": "auth module"},
        main_code="print('hi')",
        integration_tests="",
        readme="# x",
        requirements="fastapi",
    )
    text = str(result.project_structure)[:1000]
    assert "app.py" in text
    assert "auth.py" in text


def test_global_review_result_defaults():
    gr = GlobalReviewResult(passed=True, score=80, summary="ok")
    assert gr.passed is True
    assert gr.score == 80


@pytest.mark.asyncio
async def test_global_reviewer_accepts_dict_project_structure():
    """dict 类型 project_structure 不应在 prompt 构建时切片报错。"""
    agent = GlobalReviewerAgent()
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(
        return_value=MagicMock(
            content='{"passed": true, "score": 90, "summary": "ok", "issues": [], "suggestions": []}'
        )
    )
    agent._llm = mock_llm

    integration = IntegrationResult(
        project_structure={"app.py": "entry", "utils.py": "helpers"},
        main_code="print('hi')",
        integration_tests="",
        readme="# x",
        requirements="fastapi",
    )
    result = await agent.review("test requirement", [], [], integration)

    assert result.passed is True
    prompt = mock_llm.ainvoke.call_args[0][0]
    assert "app.py" in prompt
    assert "utils.py" in prompt
