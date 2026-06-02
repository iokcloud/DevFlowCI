"""Tests for review_repair_hints."""

from workflow.review_repair_hints import augment_review_repair_hints


def test_adds_exception_handling_hint():
    fb = augment_review_repair_hints(
        ["get_trends 静默返回空列表掩盖解析失败"],
        "base feedback",
    )
    assert "禁止 except Exception: return []" in fb
    assert "工作流针对性修复提示" in fb


def test_adds_validation_hint():
    fb = augment_review_repair_hints(
        ["缺少对 rank 数值范围的校验"],
        "base",
    )
    assert "_validated_trend" in fb
    assert "rank" in fb


def test_no_hint_when_unmatched():
    fb = augment_review_repair_hints(["代码风格缩进不一致"], "only base")
    assert fb == "only base"


def test_adds_missing_api_hint():
    fb = augment_review_repair_hints(
        ["缺少对外暴露的 get_trends() 与 get_risks() 接口定义"],
        "base",
    )
    assert "get_trends(filepath)" in fb
    assert "薄包装" in fb


def test_adds_typo_hint():
    fb = augment_review_repair_hints(
        ["_parse_risks_md 中 descrip 拼写错误"],
        "base",
    )
    assert "descrip" in fb
    assert "description" in fb
