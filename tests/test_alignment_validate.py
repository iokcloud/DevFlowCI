"""AlignmentAgent.validate_alignment() 单元测试。

覆盖：必填字段、plan 数组校验、依赖交叉验证、insufficient_info 等。
"""

from __future__ import annotations

import pytest

from agents.alignment_agent import AlignmentAgent


# ── 辅助工厂函数 ────────────────────────────────────────────

def _make_alignment(
    *,
    summary: str = "测试摘要",
    assumptions: list[str] | None = None,
    risks: list[str] | None = None,
    plan: list[dict] | None = None,
    questions: list[str] | None = None,
    status: str | None = None,
    message: str | None = None,
) -> dict:
    result: dict = {}
    if status:
        result["status"] = status
    if message:
        result["message"] = message
    if summary:
        result["summary"] = summary
    result["assumptions"] = assumptions or []
    result["risks"] = risks or []
    result["plan"] = plan or []
    result["questions"] = questions or []
    return result


def _make_plan_item(
    module: str,
    description: str = "测试描述",
    reason: str = "测试理由",
    dependencies: list[str] | None = None,
) -> dict:
    item: dict = {"module": module, "description": description, "reason": reason}
    if dependencies is not None:
        item["dependencies"] = dependencies
    return item


# ── 正常通过 ────────────────────────────────────────────────

class TestValidAlignment:
    def test_minimal_valid(self) -> None:
        result = _make_alignment(
            plan=[_make_plan_item("main")],
        )
        assert AlignmentAgent.validate_alignment(result) == []

    def test_multiple_modules_no_deps(self) -> None:
        result = _make_alignment(
            plan=[
                _make_plan_item("auth"),
                _make_plan_item("api"),
                _make_plan_item("db"),
            ],
        )
        assert AlignmentAgent.validate_alignment(result) == []

    def test_with_valid_dependencies(self) -> None:
        result = _make_alignment(
            plan=[
                _make_plan_item("auth"),
                _make_plan_item("api", dependencies=["auth"]),
                _make_plan_item("db"),
            ],
        )
        assert AlignmentAgent.validate_alignment(result) == []

    def test_insufficient_info_valid(self) -> None:
        result = {
            "status": "insufficient_info",
            "message": "资料不足，无法生成计划",
        }
        assert AlignmentAgent.validate_alignment(result) == []


# ── 必填字段 ────────────────────────────────────────────────

class TestRequiredFields:
    def test_missing_summary(self) -> None:
        result = _make_alignment(summary="", plan=[_make_plan_item("m")])
        errors = AlignmentAgent.validate_alignment(result)
        assert any("summary" in e for e in errors)

    def test_missing_plan_field(self) -> None:
        result = {
            "summary": "test",
            "assumptions": [],
            "risks": [],
            "questions": [],
        }
        errors = AlignmentAgent.validate_alignment(result)
        assert any("plan" in e for e in errors)

    def test_plan_not_list(self) -> None:
        result = _make_alignment(plan="not_a_list")  # type: ignore[arg-type]
        errors = AlignmentAgent.validate_alignment(result)
        assert any("plan" in e for e in errors)

    def test_missing_module_field(self) -> None:
        result = _make_alignment(
            plan=[{"description": "x", "reason": "y"}],
        )
        errors = AlignmentAgent.validate_alignment(result)
        assert any("module" in e for e in errors)

    def test_missing_description_field(self) -> None:
        result = _make_alignment(
            plan=[{"module": "m", "reason": "y"}],
        )
        errors = AlignmentAgent.validate_alignment(result)
        assert any("description" in e for e in errors)

    def test_missing_reason_field(self) -> None:
        result = _make_alignment(
            plan=[{"module": "m", "description": "x"}],
        )
        errors = AlignmentAgent.validate_alignment(result)
        assert any("reason" in e for e in errors)

    def test_insufficient_info_missing_message(self) -> None:
        result = {"status": "insufficient_info"}
        errors = AlignmentAgent.validate_alignment(result)
        assert any("message" in e for e in errors)


# ── 依赖交叉验证 ────────────────────────────────────────────

class TestDependencyValidation:
    def test_dep_not_exist(self) -> None:
        result = _make_alignment(
            plan=[
                _make_plan_item("api_server", dependencies=["llm_client", "prompt_service"]),
            ],
        )
        errors = AlignmentAgent.validate_alignment(result)
        assert len(errors) == 2
        assert any("llm_client" in e for e in errors)
        assert any("prompt_service" in e for e in errors)

    def test_partial_missing_dep(self) -> None:
        result = _make_alignment(
            plan=[
                _make_plan_item("auth"),
                _make_plan_item("api", dependencies=["auth", "cache"]),
            ],
        )
        errors = AlignmentAgent.validate_alignment(result)
        assert len(errors) == 1
        assert "cache" in errors[0]

    def test_no_deps_key(self) -> None:
        """没有 dependencies 字段不应报错。"""
        result = _make_alignment(
            plan=[_make_plan_item("main")],
        )
        errors = AlignmentAgent.validate_alignment(result)
        assert len(errors) == 0

    def test_empty_deps_list(self) -> None:
        result = _make_alignment(
            plan=[_make_plan_item("main", dependencies=[])],
        )
        errors = AlignmentAgent.validate_alignment(result)
        assert len(errors) == 0

    def test_deps_is_string_not_list(self) -> None:
        """dependencies 为非 list 类型时不应 crash，但也不应伪造校验。"""
        result = _make_alignment(
            plan=[_make_plan_item("main", dependencies="auth")],  # type: ignore[arg-type]
        )
        errors = AlignmentAgent.validate_alignment(result)
        # 不是 list 就跳过依赖校验（不 crash）
        assert not any("依赖不存在" in e for e in errors)

    def test_combined_missing_field_and_dep_error(self) -> None:
        """同时报告字段缺失和依赖错误。"""
        result = _make_alignment(
            plan=[
                _make_plan_item("api", dependencies=["ghost"]),
                {"reason": "missing module and description"},
            ],
        )
        errors = AlignmentAgent.validate_alignment(result)
        assert any("ghost" in e for e in errors)
        assert any("module" in e for e in errors)
