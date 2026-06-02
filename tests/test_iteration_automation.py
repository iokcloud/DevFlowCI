"""迭代自动化：保留代码、fixtures、自动补充说明。"""

from __future__ import annotations

from workflow.iteration_automation import (
    build_auto_iterate_addendum,
    build_checklist_repair_feedback,
    detect_fixture_needs,
    ensure_delivery_fixtures,
    is_preserve_worthy_code,
    is_stub_code,
    merge_addenda,
    merge_requirements_text,
    refresh_blocked_failure_reason,
    resolve_blocked_artifacts,
)


def test_is_stub_and_preserve():
    assert is_stub_code("raise NotImplementedError('blocked')")
    good = "def get_trends(path: str):\n    return []\n" * 3
    assert is_preserve_worthy_code(good, "def test_x():\n    assert True\n")
    assert not is_preserve_worthy_code("", "")


def test_build_checklist_includes_prior_code():
    fb = build_checklist_repair_feedback(
        ["docstring 不完整"],
        prior_code="def foo():\n    return 1\n" * 5,
        prior_test="def test_foo():\n    assert foo() == 1\n",
        module_name="report_dashboard",
    )
    assert "审查修复清单" in fb
    assert "report_dashboard" in fb
    assert "def foo" in fb
    assert "禁止" in fb


def test_auto_iterate_addendum():
    text = build_auto_iterate_addendum(
        "report_dashboard",
        "解析 docx 与 md，暴露 get_trends",
        ["缺少后缀校验"],
        iteration=3,
    )
    assert "report_dashboard" in text
    assert "禁止 NotImplementedError" in text
    assert "fixtures/" in text


def test_merge_addenda():
    merged = merge_addenda("用户补充", "自动补充")
    assert "用户补充" in merged
    assert "自动补充" in merged


def test_detect_fixture_needs():
    needs = detect_fixture_needs("解析 docx 热搜与 md 避雷报告")
    exts = {n[0] for n in needs}
    assert ".docx" in exts
    assert ".md" in exts


def test_ensure_delivery_fixtures(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "workflow.iteration_automation.DELIVERIES_DIR",
        tmp_path,
    )
    paths = ensure_delivery_fixtures(
        "proj-test",
        "解析 docx 与 md 报告",
    )
    assert len(paths) == 2
    base = tmp_path / "proj-test" / "fixtures"
    assert (base / "热搜话题分析.docx").is_file()
    assert (base / "交易数据分析.md").is_file()


def test_resolve_blocked_preserves_code():
    good = "def get_trends():\n    return []\n" * 4

    def stub_gen(*_a, **_k):
        return "raise NotImplementedError"

    code, test, used_stub = resolve_blocked_artifacts(
        module_name="m",
        module_type="backend",
        description="d",
        failure_reason="审查失败",
        code_candidates=["", good],
        test_candidates=["", "def test_m(): pass"],
        stub_generator=stub_gen,
    )
    assert code == good
    assert not used_stub


def test_resolve_blocked_falls_back_to_stub():
    def stub_gen(name, mtype, desc, reason):
        return f"STUB:{name}"

    code, _test, used_stub = resolve_blocked_artifacts(
        module_name="m",
        module_type="backend",
        description="d",
        failure_reason="fail",
        code_candidates=["raise NotImplementedError"],
        test_candidates=[""],
        stub_generator=stub_gen,
    )
    assert used_stub
    assert code.startswith("STUB:")


def test_merge_requirements_dedup():
    base = "fastapi>=0.115.0\n"
    merged = merge_requirements_text(base, ["python-docx>=1.1.0", "python-docx>=1.2.0"])
    assert merged.count("python-docx") == 1


def test_build_checklist_includes_targeted_hints():
    fb = build_checklist_repair_feedback(
        ["静默返回空列表，掩盖文件损坏"],
        prior_code="def get_trends(p):\n    return []\n" * 5,
        module_name="report_dashboard",
    )
    assert "工作流针对性修复提示" in fb
    assert "禁止 except Exception" in fb


def test_refresh_blocked_failure_reason_strips_stale_truncation():
    code = (
        "def get_risks(path: str):\n"
        "    return []\n"
        "def get_trends(path: str):\n"
        "    return []\n"
    )
    test = "def test_x():\n    assert True\n"
    reason = refresh_blocked_failure_reason(
        code,
        test,
        ["代码截断不完整，get_risks 中 logger.warning 未闭合", "缺少后缀校验"],
        "旧 failure",
    )
    assert "截断" not in reason
    assert "后缀校验" in reason
