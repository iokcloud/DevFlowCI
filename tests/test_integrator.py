"""Tests for integrator global reviewer string handling."""

from __future__ import annotations

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
