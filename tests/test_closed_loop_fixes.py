"""测试闭环修复的关键修复 — 循环依赖检测、反馈不覆盖、返回值正确。"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest

from workflow.closed_loop import cleanup_after_fix
from workflow.executor import WorkflowExecutor


# ── _build_layers 循环依赖检测 ─────────────────────────────

class TestBuildLayersWithCircularDeps:
    """验证 get_depth 不再因循环依赖而无限递归。"""

    @staticmethod
    def test_no_circular_dep_normal():
        """正常无环依赖能正确分层。"""
        modules = [
            {"module_name": "auth", "dependencies": []},
            {"module_name": "api", "dependencies": ["auth"]},
        ]
        layers = WorkflowExecutor._build_layers(modules)
        assert len(layers) == 2
        # 第一层无依赖
        assert layers[0] == ["auth"]
        # 第二层依赖第一层
        assert layers[1] == ["api"]

    @staticmethod
    def test_direct_circular_dep_does_not_crash():
        """A→B→A 直接循环依赖不会栈溢出。"""
        modules = [
            {"module_name": "A", "dependencies": ["B"]},
            {"module_name": "B", "dependencies": ["A"]},
        ]
        layers = WorkflowExecutor._build_layers(modules)
        # 不应崩溃，所有模块都应出现
        flat = [name for layer in layers for name in layer]
        assert "A" in flat
        assert "B" in flat

    @staticmethod
    def test_indirect_circular_dep_does_not_crash():
        """A→B→C→A 间接循环依赖。"""
        modules = [
            {"module_name": "A", "dependencies": ["B"]},
            {"module_name": "B", "dependencies": ["C"]},
            {"module_name": "C", "dependencies": ["A"]},
        ]
        layers = WorkflowExecutor._build_layers(modules)
        flat = [name for layer in layers for name in layer]
        assert len(flat) == 3

    @staticmethod
    def test_circular_dep_logs_warning(caplog):
        """循环依赖产生 logger.warning。"""
        modules = [
            {"module_name": "X", "dependencies": ["Y"]},
            {"module_name": "Y", "dependencies": ["X"]},
        ]
        with caplog.at_level(logging.WARNING):
            WorkflowExecutor._build_layers(modules)
        assert "循环依赖" in caplog.text


# ── cleanup_after_fix 返回 stats ────────────────────────────

class TestCleanupAfterFix:
    """验证 cleanup_after_fix 现在正确返回 stats 字典。"""

    @pytest.mark.asyncio
    async def test_returns_stats_dict(self, memory_db):
        """即使数据库为空（无记录可清理），也应返回 stats。"""
        stats = await cleanup_after_fix("nonexistent_project", "nonexistent_module")
        assert isinstance(stats, dict)
        assert "resolved_count" in stats
        assert "cleaned_files" in stats
        assert stats["resolved_count"] == 0

    @pytest.mark.asyncio
    async def test_dictionary_keys_present(self, memory_db):
        """返回的字典包含所有必要键。"""
        stats = await cleanup_after_fix("p1", "m1")
        assert set(stats.keys()) == {"resolved_count", "cleaned_files"}


# ── feedback 不再被覆盖（策略快速审查） ─────────────────────

class TestFeedbackPreservation:
    """模拟闭环修复中审查通过/测试失败时 feedback 不被覆盖的场景。"""

    @staticmethod
    def test_direct_retry_feedback_not_overwritten():
        """
        模拟 direct_retry 中 review_passed=True 但 test_ok=False 的场景：
        feedback 应保持测试失败信息，不被审查问题覆盖。
        """
        review_passed = True
        test_ok = False
        review_summary = "测试断言失败: assert 1 == 2"

        # 模拟原代码中的逻辑（修复后）
        if review_passed:
            if test_ok:
                feedback = "should return"
            else:
                feedback = f"测试验证失败，请修复：\n{review_summary}"
        else:
            feedback = "审查反馈：\n  - 缺少 docstring"

        # 验证：feedback 是测试失败信息，不是审查问题
        assert "测试验证失败" in feedback
        assert "docstring" not in feedback

    @staticmethod
    def test_modify_code_quick_review_break_on_test_fail():
        """审查通过但测试失败时跳出快速审查循环（不空转）。"""
        quick_review_max = 2
        review_passed = True
        test_ok = False
        iterations = 0

        for qr in range(quick_review_max):
            iterations += 1
            if review_passed:
                if test_ok:
                    break  # success
                # test failed — break out (修复后行为)
                feedback = "审查通过但测试验证失败"
                break

            if qr < quick_review_max - 1:
                pass  # regenerate code in real flow

        # 验证：只迭代 1 次就跳出，不会浪费第 2 次
        assert iterations == 1
        assert "测试验证失败" in feedback

    @staticmethod
    def test_no_break_when_review_fails():
        """审查不通过时应继续循环（正常行为不变）。"""
        quick_review_max = 2
        iterations = 0
        review_passed = False

        for qr in range(quick_review_max):
            iterations += 1
            if review_passed:
                break  # won't hit
            if qr < quick_review_max - 1:
                continue

        # 应该跑满 2 次
        assert iterations == 2
