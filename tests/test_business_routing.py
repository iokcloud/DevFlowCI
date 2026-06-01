"""Tests for main.py business tech requirement helper."""

from __future__ import annotations

from main import _build_business_tech_requirement


def test_is_prime_short_path_single_module():
    req, cap = _build_business_tech_requirement(
        "商业调研 + is_prime MVP 验证",
        {"executive_summary": "摘要", "recommendations": "建议"},
    )
    assert cap == 1
    assert "is_prime" in req


def test_business_plan_default_mvp_cap():
    req, cap = _build_business_tech_requirement(
        "市场规模分析",
        {
            "executive_summary": "银发经济",
            "recommendations": "健康管理 App",
            "roadmap": [{"phase": "MVP", "actions": ["原型"]}],
        },
    )
    assert cap == 3
    assert "银发经济" in req
    assert "MVP约束" in req
