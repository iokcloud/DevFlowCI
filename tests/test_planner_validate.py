"""PlannerAgent.validate_plan() 单元测试。

覆盖：缺少字段、依赖不存在、循环依赖、边界场景等。
"""

from __future__ import annotations

from agents.planner import PlannerAgent

# ── 辅助工厂函数 ────────────────────────────────────────────

def _make_plan(modules: list[dict]) -> dict:
    return {"modules": modules, "global_requirements": []}


def _make_module(
    name: str,
    description: str = "test desc",
    mod_type: str = "backend",
    dependencies: list[str] | None = None,
) -> dict:
    return {
        "module_name": name,
        "description": description,
        "type": mod_type,
        "dependencies": dependencies or [],
    }


# ── 正常通过 ────────────────────────────────────────────────

class TestValidPlans:
    def test_single_module_no_deps(self) -> None:
        plan = _make_plan([_make_module("main")])
        assert PlannerAgent.validate_plan(plan) == []

    def test_two_modules_with_valid_dep(self) -> None:
        plan = _make_plan([
            _make_module("auth"),
            _make_module("api", dependencies=["auth"]),
        ])
        assert PlannerAgent.validate_plan(plan) == []

    def test_chain_dependency(self) -> None:
        plan = _make_plan([
            _make_module("db"),
            _make_module("auth", dependencies=["db"]),
            _make_module("api", dependencies=["auth"]),
        ])
        assert PlannerAgent.validate_plan(plan) == []

    def test_diamond_dependency(self) -> None:
        plan = _make_plan([
            _make_module("base"),
            _make_module("a", dependencies=["base"]),
            _make_module("b", dependencies=["base"]),
            _make_module("c", dependencies=["a", "b"]),
        ])
        assert PlannerAgent.validate_plan(plan) == []


# ── 缺少 modules 字段 ──────────────────────────────────────

class TestMissingModulesField:
    def test_no_modules_key(self) -> None:
        errors = PlannerAgent.validate_plan({})
        assert "缺少 'modules' 字段" in errors

    def test_modules_not_list(self) -> None:
        errors = PlannerAgent.validate_plan({"modules": "not_a_list"})
        assert "'modules' 必须是数组" in errors

    def test_modules_empty_list(self) -> None:
        errors = PlannerAgent.validate_plan({"modules": []})
        assert "'modules' 不能为空" in errors


# ── 模块必填字段 ───────────────────────────────────────────

class TestModuleRequiredFields:
    def test_missing_module_name(self) -> None:
        plan = _make_plan([{"description": "x", "type": "backend", "dependencies": []}])
        errors = PlannerAgent.validate_plan(plan)
        assert any("module_name" in e for e in errors)

    def test_missing_description(self) -> None:
        plan = _make_plan([{"module_name": "a", "type": "backend"}])
        errors = PlannerAgent.validate_plan(plan)
        assert any("description" in e for e in errors)

    def test_missing_type(self) -> None:
        plan = _make_plan([{"module_name": "a", "description": "x"}])
        errors = PlannerAgent.validate_plan(plan)
        assert any("type" in e for e in errors)

    def test_empty_module_name(self) -> None:
        plan = _make_plan([{"module_name": "", "description": "x", "type": "backend"}])
        errors = PlannerAgent.validate_plan(plan)
        assert any("module_name" in e for e in errors)

    def test_duplicate_module_name(self) -> None:
        plan = _make_plan([
            _make_module("auth"),
            _make_module("auth"),
        ])
        errors = PlannerAgent.validate_plan(plan)
        assert any("重复" in e for e in errors)


# ── 依赖有效性 ─────────────────────────────────────────────

class TestDependencyValidation:
    def test_deps_not_list(self) -> None:
        plan = _make_plan([
            _make_module("a", dependencies="not_a_list"),  # type: ignore[arg-type]
        ])
        errors = PlannerAgent.validate_plan(plan)
        assert any("dependencies 必须是数组" in e for e in errors)

    def test_dependency_target_not_exist(self) -> None:
        plan = _make_plan([
            _make_module("api_server", dependencies=["llm_client", "prompt_service", "report_dashboard"]),
        ])
        errors = PlannerAgent.validate_plan(plan)
        # 应该报告每个不存在的依赖
        assert len(errors) == 3
        assert any("llm_client" in e for e in errors)
        assert any("prompt_service" in e for e in errors)
        assert any("report_dashboard" in e for e in errors)

    def test_partial_missing_dependency(self) -> None:
        """部分依赖存在，部分不存在。"""
        plan = _make_plan([
            _make_module("auth"),
            _make_module("api", dependencies=["auth", "cache"]),  # cache 不存在
        ])
        errors = PlannerAgent.validate_plan(plan)
        assert len(errors) == 1
        assert "cache" in errors[0]
        assert "api" in errors[0]

    def test_all_deps_exist(self) -> None:
        plan = _make_plan([
            _make_module("auth"),
            _make_module("db"),
            _make_module("api", dependencies=["auth", "db"]),
        ])
        assert PlannerAgent.validate_plan(plan) == []

    def test_dependency_on_passed_external_module(self) -> None:
        """增量开发：依赖项目已通过模块时不应报错。"""
        plan = _make_plan([
            _make_module(
                "web_app",
                dependencies=["llm_client", "prompt_service"],
            ),
        ])
        external = {"llm_client", "prompt_service"}
        assert PlannerAgent.validate_plan(plan, external) == []

    def test_external_module_does_not_fix_unknown_dep(self) -> None:
        plan = _make_plan([
            _make_module("api", dependencies=["llm_client", "ghost"]),
        ])
        errors = PlannerAgent.validate_plan(plan, {"llm_client"})
        assert len(errors) == 1
        assert "ghost" in errors[0]


# ── 依赖自动修剪 ───────────────────────────────────────────

class TestSanitizePlanDependencies:
    def test_strips_unknown_keeps_plan_and_external(self) -> None:
        plan = _make_plan([
            _make_module("auth"),
            _make_module(
                "api",
                dependencies=["auth", "llm_client", "ghost"],
            ),
        ])
        removed = PlannerAgent.sanitize_plan_dependencies(
            plan, {"llm_client"}
        )
        assert "ghost" in removed[0]
        assert plan["modules"][1]["dependencies"] == ["auth", "llm_client"]

    def test_sanitize_then_validate_passes(self) -> None:
        plan = _make_plan([
            _make_module(
                "polish_api",
                dependencies=["llm_client", "prompt_service"],
            ),
        ])
        external = {"llm_client", "prompt_service"}
        PlannerAgent.sanitize_plan_dependencies(plan, external)
        assert PlannerAgent.validate_plan(plan, external) == []


# ── 循环依赖 ───────────────────────────────────────────────

class TestCycleDetection:
    def test_direct_cycle(self) -> None:
        """A → B → A"""
        plan = _make_plan([
            _make_module("a", dependencies=["b"]),
            _make_module("b", dependencies=["a"]),
        ])
        errors = PlannerAgent.validate_plan(plan)
        assert any("循环依赖" in e for e in errors)

    def test_indirect_cycle(self) -> None:
        """A → B → C → A"""
        plan = _make_plan([
            _make_module("a", dependencies=["b"]),
            _make_module("b", dependencies=["c"]),
            _make_module("c", dependencies=["a"]),
        ])
        errors = PlannerAgent.validate_plan(plan)
        assert any("循环依赖" in e for e in errors)

    def test_self_loop(self) -> None:
        """A → A"""
        plan = _make_plan([
            _make_module("a", dependencies=["a"]),
        ])
        errors = PlannerAgent.validate_plan(plan)
        assert any("循环依赖" in e for e in errors)

    def test_no_false_positive_on_dag(self) -> None:
        plan = _make_plan([
            _make_module("a"),
            _make_module("b", dependencies=["a"]),
            _make_module("c", dependencies=["a", "b"]),
        ])
        errors = PlannerAgent.validate_plan(plan)
        assert not any("循环依赖" in e for e in errors)

    def test_cycle_and_missing_dep_combined(self) -> None:
        """同时有循环依赖和不存在的依赖——两者都应报告。"""
        plan = _make_plan([
            _make_module("a", dependencies=["b"]),
            _make_module("b", dependencies=["a", "ghost"]),
        ])
        errors = PlannerAgent.validate_plan(plan)
        assert any("循环依赖" in e for e in errors)
        assert any("ghost" in e for e in errors)
