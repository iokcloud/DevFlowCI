"""迭代改进：需求合并与状态重建。"""

from __future__ import annotations

import json

from workflow.requirement_context import (
    append_requirement_addendum,
    build_effective_requirement,
    build_iteration_context,
    parse_requirement_addenda,
)
from workflow.state_builder import module_task_to_result


def test_parse_and_append_addendum():
    raw = append_requirement_addendum(None, text="补充 A", round_num=2)
    entries = parse_requirement_addenda(raw)
    assert len(entries) == 1
    assert entries[0]["text"] == "补充 A"
    assert entries[0]["round"] == "2"

    raw2 = append_requirement_addendum(raw, text="补充 B", round_num=3)
    entries2 = parse_requirement_addenda(raw2)
    assert len(entries2) == 2
    assert entries2[1]["text"] == "补充 B"


def test_build_effective_requirement():
    merged = build_effective_requirement(
        "原始需求",
        [{"round": "2", "text": "加 dashboard"}],
    )
    assert "原始需求" in merged
    assert "加 dashboard" in merged
    assert "第 2 轮补充" in merged


def test_build_iteration_context():
    ctx = build_iteration_context(
        iteration=2,
        addendum_text="修复 dashboard",
        global_review={"score": 65, "issues": ["blocked mod"]},
    )
    assert "第 2 轮" in ctx
    assert "65/100" in ctx
    assert "修复 dashboard" in ctx


def test_module_task_to_result_minimal():
    class FakeMod:
        status = type("S", (), {"value": "blocked"})()
        spec = json.dumps({"name": "x"})
        code = "print(1)"
        tests = "def test_x(): pass"
        retry_count = 2
        failure_reason = "timeout"

    r = module_task_to_result(FakeMod())
    assert r["status"] == "blocked"
    assert r["code"] == "print(1)"
    assert r["spec"]["name"] == "x"
