"""BusinessPlannerAgent.validate() 单元测试。

覆盖：必填字段、嵌套对象类型、roadmap 最小阶段数、risks severity 枚举等。
"""

from __future__ import annotations

import pytest

from agents.business_planner import BusinessPlannerAgent


# ── 辅助工厂函数 ────────────────────────────────────────────

def _make_valid() -> dict:
    return {
        "executive_summary": "这是一个基于AI的智能客服系统的商业计划概述。",
        "market_analysis": {
            "target_audience": "希望降低客服成本的中小型电商企业",
            "competition": "市场上已有Zendesk、Intercom等产品，但价格偏高",
            "trends": "AI客服市场年增长率超过25%",
        },
        "product_positioning": "面向中小企业的低成本智能客服SaaS平台",
        "business_model": {
            "revenue_streams": ["按月订阅", "按量计费", "企业定制"],
            "cost_structure": "研发人力成本占60%，云服务占30%",
            "channels": ["线上直销", "代理商渠道"],
        },
        "roadmap": [
            {
                "phase": "MVP开发",
                "duration": "3个月",
                "actions": ["搭建基础架构", "实现核心问答引擎"],
                "milestones": ["MVP发布"],
            },
            {
                "phase": "市场推广",
                "duration": "6个月",
                "actions": ["BD拓展", "内容营销"],
                "milestones": ["获得100个付费客户"],
            },
        ],
        "risks_and_mitigations": [
            {
                "risk": "大模型幻觉导致回答不准确",
                "severity": "high",
                "mitigation": "建立知识库+RAG检索增强",
            },
            {
                "risk": "客户获取成本过高",
                "severity": "medium",
                "mitigation": "优化SEO和内容营销策略",
            },
        ],
        "recommendations": "建议先从电商客服细分市场切入，建立标杆客户后再横向扩展。",
    }


# ── 正常通过 ────────────────────────────────────────────────

class TestValidBusinessPlan:
    def test_full_valid(self) -> None:
        assert BusinessPlannerAgent.validate(_make_valid()) == []

    def test_minimal_valid(self) -> None:
        """至少2个阶段、每个阶段有1个action/milestone即可。"""
        plan = _make_valid()
        plan["roadmap"] = [
            {"phase": "阶段1", "duration": "1月", "actions": ["a"], "milestones": ["m"]},
            {"phase": "阶段2", "duration": "2月", "actions": ["b"], "milestones": ["n"]},
        ]
        assert BusinessPlannerAgent.validate(plan) == []


# ── 顶层必填字段 ────────────────────────────────────────────

class TestRequiredTopLevelFields:
    def test_missing_summary(self) -> None:
        plan = _make_valid()
        del plan["executive_summary"]
        errors = BusinessPlannerAgent.validate(plan)
        assert any("executive_summary" in e for e in errors)

    def test_empty_summary(self) -> None:
        plan = _make_valid()
        plan["executive_summary"] = ""
        errors = BusinessPlannerAgent.validate(plan)
        assert any("executive_summary" in e for e in errors)

    def test_missing_positioning(self) -> None:
        plan = _make_valid()
        del plan["product_positioning"]
        errors = BusinessPlannerAgent.validate(plan)
        assert any("product_positioning" in e for e in errors)

    def test_missing_recommendations(self) -> None:
        plan = _make_valid()
        del plan["recommendations"]
        errors = BusinessPlannerAgent.validate(plan)
        assert any("recommendations" in e for e in errors)


# ── market_analysis ─────────────────────────────────────────

class TestMarketAnalysis:
    def test_not_dict(self) -> None:
        plan = _make_valid()
        plan["market_analysis"] = "not an object"
        errors = BusinessPlannerAgent.validate(plan)
        assert any("market_analysis" in e for e in errors)

    def test_missing_subfields(self) -> None:
        plan = _make_valid()
        plan["market_analysis"] = {}
        errors = BusinessPlannerAgent.validate(plan)
        assert len(errors) == 3  # target_audience, competition, trends

    def test_empty_subfield(self) -> None:
        plan = _make_valid()
        plan["market_analysis"]["target_audience"] = ""
        errors = BusinessPlannerAgent.validate(plan)
        assert any("target_audience" in e for e in errors)


# ── business_model ──────────────────────────────────────────

class TestBusinessModel:
    def test_not_dict(self) -> None:
        plan = _make_valid()
        plan["business_model"] = []
        errors = BusinessPlannerAgent.validate(plan)
        assert any("business_model" in e for e in errors)

    def test_revenue_streams_not_list(self) -> None:
        plan = _make_valid()
        plan["business_model"]["revenue_streams"] = "subscription"
        errors = BusinessPlannerAgent.validate(plan)
        assert any("revenue_streams" in e for e in errors)

    def test_missing_cost_structure(self) -> None:
        plan = _make_valid()
        del plan["business_model"]["cost_structure"]
        errors = BusinessPlannerAgent.validate(plan)
        assert any("cost_structure" in e for e in errors)


# ── roadmap ─────────────────────────────────────────────────

class TestRoadmap:
    def test_not_list(self) -> None:
        plan = _make_valid()
        plan["roadmap"] = {}
        errors = BusinessPlannerAgent.validate(plan)
        assert any("roadmap" in e for e in errors)

    def test_too_few_phases(self) -> None:
        plan = _make_valid()
        plan["roadmap"] = [
            {"phase": "唯一阶段", "duration": "1月", "actions": ["a"], "milestones": ["m"]},
        ]
        errors = BusinessPlannerAgent.validate(plan)
        assert any("至少需要 2 个阶段" in e for e in errors)

    def test_phase_missing_fields(self) -> None:
        plan = _make_valid()
        plan["roadmap"][0] = {"phase": "无duration"}
        errors = BusinessPlannerAgent.validate(plan)
        assert any("duration" in e for e in errors)

    def test_phase_empty_actions(self) -> None:
        plan = _make_valid()
        plan["roadmap"][0]["actions"] = []
        errors = BusinessPlannerAgent.validate(plan)
        assert any("actions" in e for e in errors)

    def test_phase_not_dict(self) -> None:
        plan = _make_valid()
        plan["roadmap"][0] = "not a dict"
        errors = BusinessPlannerAgent.validate(plan)
        assert any("roadmap #0" in e for e in errors)


# ── risks_and_mitigations ───────────────────────────────────

class TestRisksAndMitigations:
    def test_not_list(self) -> None:
        plan = _make_valid()
        plan["risks_and_mitigations"] = {}
        errors = BusinessPlannerAgent.validate(plan)
        assert any("risks_and_mitigations" in e for e in errors)

    def test_missing_risk_field(self) -> None:
        plan = _make_valid()
        plan["risks_and_mitigations"][0] = {"severity": "high"}
        errors = BusinessPlannerAgent.validate(plan)
        assert any("risk" in e for e in errors)

    def test_invalid_severity(self) -> None:
        plan = _make_valid()
        plan["risks_and_mitigations"][0]["severity"] = "critical"
        errors = BusinessPlannerAgent.validate(plan)
        assert any("severity" in e for e in errors)

    def test_severity_case_sensitive(self) -> None:
        plan = _make_valid()
        plan["risks_and_mitigations"][0]["severity"] = "High"
        errors = BusinessPlannerAgent.validate(plan)
        assert any("severity" in e for e in errors)

    def test_risk_item_not_dict(self) -> None:
        plan = _make_valid()
        plan["risks_and_mitigations"][0] = "just a string"
        errors = BusinessPlannerAgent.validate(plan)
        assert any("risks_and_mitigations #0" in e for e in errors)


# ── 组合错误 ────────────────────────────────────────────────

class TestCombinedErrors:
    def test_multiple_categories(self) -> None:
        """多个维度同时出错应全部报告。"""
        plan: dict = {}
        errors = BusinessPlannerAgent.validate(plan)
        # 至少报告 summary + positioning + recommendations + market_analysis + business_model + roadmap + risks
        assert len(errors) >= 6
