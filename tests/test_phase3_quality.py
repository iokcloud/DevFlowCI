"""Tests for config defaults, JSON extraction edge cases, and new modules."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from config import (
    AUTO_FIX_ENABLED,
    BUSINESS_MVP_MAX_MODULES,
    CONTEXT_ANALYSIS_TOKEN_LIMIT,
    DELIVERIES_DIR,
    DOCS_DIR,
    MAX_REVIEW_RETRIES,
    MEMORY_DIR,
    PROJECT_ROOT,
    STATIC_DIR,
    ErrorType,
)
from workflow.mvp_utils import (
    _apply_mvp_module_cap,
    _exec_tests_passed,
    _exec_tests_skipped,
    _module_clear_to_pass,
    _resolve_mvp_module_cap,
    _sanitize_business_multi_modules,
)


class TestConfigDefaults:
    """验证 config.py 默认值的一致性。"""

    def test_paths_exist(self):
        assert DELIVERIES_DIR.exists()
        assert STATIC_DIR.exists()
        assert MEMORY_DIR.exists()
        assert DOCS_DIR.exists()
        assert PROJECT_ROOT.exists()

    def test_paths_are_absolute(self):
        assert DELIVERIES_DIR.is_absolute()
        assert STATIC_DIR.is_absolute()
        assert PROJECT_ROOT.is_absolute()

    def test_error_type_enum_complete(self):
        assert ErrorType.API_TIMEOUT == "api_timeout"
        assert ErrorType.SYNTAX_ERROR == "syntax_error"
        assert ErrorType.TEST_FAILURE == "test_failure"
        assert len(ErrorType) >= 7

    def test_numeric_config_reasonable(self):
        assert MAX_REVIEW_RETRIES >= 3
        assert CONTEXT_ANALYSIS_TOKEN_LIMIT >= 1000
        assert BUSINESS_MVP_MAX_MODULES >= 1

    def test_boolean_flags_typed(self):
        assert isinstance(AUTO_FIX_ENABLED, bool)


class TestJsonExtraction:
    """JSON 提取边界情况。"""

    def test_simple_object(self):
        from utils import extract_json
        result = extract_json('{"name": "test"}')
        assert result["name"] == "test"

    def test_with_code_fence(self):
        from utils import extract_json
        result = extract_json('```json\n{"key": "value"}\n```')
        assert result["key"] == "value"

    def test_with_extra_text(self):
        from utils import extract_json
        result = extract_json('Some text {"a": 1} more text')
        assert result["a"] == 1

    def test_truncated_json_repaired(self):
        from utils import extract_json
        # 缺少闭合花括号的截断 JSON 可被修复
        result = extract_json('{"name": "hello", "value": 42')
        assert result["name"] == "hello"

    def test_nested_object(self):
        from utils import extract_json
        result = extract_json('{"user": {"name": "alice", "age": 30}}')
        assert result["user"]["name"] == "alice"

    def test_empty_string_raises(self):
        from utils import extract_json
        with pytest.raises(ValueError):
            extract_json("not json at all")


class TestMvpUtils:
    """商业 MVP 辅助函数。"""

    def test_resolve_mvp_cap_from_state(self):
        state = {"mvp_max_modules": 2}
        assert _resolve_mvp_module_cap(state) == 2

    def test_resolve_mvp_cap_default_business(self):
        state = {"requirement": "build a SaaS", "mode": "business"}
        cap = _resolve_mvp_module_cap(state)
        assert cap is None or cap >= 1

    def test_exec_tests_passed_with_none(self):
        assert _exec_tests_passed(None) is False

    def test_exec_tests_passed_with_failures(self):
        assert _exec_tests_passed({"failed": 1}) is False

    def test_exec_tests_passed_success(self):
        assert _exec_tests_passed({"passed": 5, "failed": 0, "total": 5}) is True

    def test_exec_tests_skipped_with_none(self):
        assert _exec_tests_skipped(None) is True  # None = no result = skipped

    def test_module_clear_to_pass_true(self):
        assert _module_clear_to_pass(True, {"failed": 0}) is True

    def test_module_clear_to_pass_false(self):
        assert _module_clear_to_pass(False, {"failed": 1}) is False

    def test_apply_mvp_cap_truncates(self):
        modules = [
            {"module_name": "a"},
            {"module_name": "b"},
            {"module_name": "c"},
            {"module_name": "d"},
        ]
        state = {"mvp_max_modules": 2}
        result = _apply_mvp_module_cap(modules, state)
        assert len(result) == 2
        assert result[0]["module_name"] == "a"

    def test_sanitize_business_below_cap(self):
        modules = [{"module_name": "core_1"}]
        state = {"mvp_max_modules": 3}
        result = _sanitize_business_multi_modules(modules, state)
        assert len(result) == 1


class TestSseBridge:
    """SSE 桥接层日志队列。"""

    def test_get_log_queue_creates(self):
        from workflow.sse_bridge import get_log_queue, remove_log_queue
        q = get_log_queue("test-project")
        assert q is not None
        remove_log_queue("test-project")

    def test_get_log_queue_reuses(self):
        from workflow.sse_bridge import get_log_queue, remove_log_queue
        q1 = get_log_queue("test-reuse")
        q2 = get_log_queue("test-reuse")
        assert q1 is q2
        remove_log_queue("test-reuse")

    def test_remove_log_queue_cleans_up(self):
        from workflow.sse_bridge import get_log_queue, remove_log_queue
        get_log_queue("test-remove")
        remove_log_queue("test-remove")
        from workflow.sse_bridge import _log_queues
        assert "test-remove" not in _log_queues
