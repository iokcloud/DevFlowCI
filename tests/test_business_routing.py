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


def test_business_plan_phase1_single_module():
    req, cap = _build_business_tech_requirement(
        "市场规模分析",
        {
            "executive_summary": "银发经济",
            "recommendations": "健康管理 App",
            "roadmap": [{"phase": "MVP", "actions": ["用户健康数据录入原型"], "milestones": ["首版可演示"]}],
        },
    )
    assert cap == 1
    assert "银发经济" in req
    assert "用户健康数据录入原型" in req
    assert "仅一个模块" in req
    assert "MVP约束" not in req


def test_business_plan_recommendations_fallback_single_module():
    req, cap = _build_business_tech_requirement(
        "市场规模分析",
        {"executive_summary": "银发经济", "recommendations": "健康管理 App"},
    )
    assert cap == 1
    assert "健康管理 App" in req


def test_business_plan_empty_alignment_uses_multi_cap():
    req, cap = _build_business_tech_requirement("仅原始需求", {})
    assert cap == 3
    assert "MVP约束" in req
