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


def test_business_plan_empty_alignment_uses_config_cap():
    req, cap = _build_business_tech_requirement("仅原始需求", {})
    assert cap == 1
    assert "MVP约束" in req


def test_business_plan_multi_module_when_cap_three(monkeypatch):
    monkeypatch.setattr("main.BUSINESS_MVP_MAX_MODULES", 3)
    req, cap = _build_business_tech_requirement(
        "市场规模分析",
        {
            "executive_summary": "银发经济",
            "roadmap": [{
                "phase": "MVP",
                "actions": ["健康录入", "订阅计费", "社区互动"],
            }],
        },
    )
    assert cap == 3
    assert "模块1" in req
    assert "模块3" in req
    assert "独立 Python 模块" in req
    assert "仅一个模块" not in req


def test_business_tech_preview_shape():
    """预览 API 与 _build_business_tech_requirement 字段一致。"""
    alignment = {
        "plan_type": "business",
        "executive_summary": "银发经济",
        "roadmap": [{"phase": "MVP", "actions": ["健康录入原型"]}],
    }
    req, cap = _build_business_tech_requirement("市场调研", alignment)
    preview = {
        "tech_requirement": req,
        "mvp_max_modules": cap,
        "estimated_modules": cap,
        "plan_type": "business",
    }
    assert preview["estimated_modules"] == 1
    assert "健康录入原型" in preview["tech_requirement"]
