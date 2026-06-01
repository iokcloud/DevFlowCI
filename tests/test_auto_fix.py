"""Unit tests for workflow.auto_fix."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from config import ErrorType
from workflow.auto_fix import (
    FixContext,
    classify_error,
    format_cases_for_prompt,
    jaccard_similarity,
    record_fix_case,
    search_similar_cases,
    fix,
)
from tests.conftest import write_json_cases


class TestClassifyError:
    def test_syntax_error(self):
        assert classify_error("SyntaxError: invalid syntax") == ErrorType.SYNTAX_ERROR

    def test_test_execution_before_timeout(self):
        assert classify_error("test execution timeout after 30s") == ErrorType.TEST_EXECUTION_FAILURE

    def test_api_timeout(self):
        assert classify_error("read timeout while calling API") == ErrorType.API_TIMEOUT

    def test_dependency_missing(self):
        assert classify_error("ModuleNotFoundError: No module named 'foo'") == ErrorType.DEPENDENCY_MISSING

    def test_review_fail_chinese(self):
        assert classify_error("代码审查不通过：缺少错误处理") == ErrorType.REVIEW_FAIL

    def test_unknown(self):
        assert classify_error("something completely unrelated") == ErrorType.UNKNOWN


class TestJaccardSimilarity:
    def test_identical(self):
        tokens = {"syntax", "error", "fix"}
        assert jaccard_similarity(tokens, tokens) == 1.0

    def test_disjoint(self):
        assert jaccard_similarity({"a", "b"}, {"c", "d"}) == 0.0

    def test_partial_overlap(self):
        sim = jaccard_similarity({"a", "b", "c"}, {"b", "c", "d"})
        assert sim == pytest.approx(0.5)


class TestAutoFixCases:
    def test_search_empty(self, tmp_auto_fix_cases: Path):
        assert search_similar_cases("syntax error") == []

    def test_record_and_search(self, tmp_auto_fix_cases: Path):
        record_fix_case(
            error_text="SyntaxError: invalid syntax in module foo",
            module_name="foo",
            module_type="backend",
            fix_summary="Fixed missing colon",
            strategy_used="modify_code",
        )
        results = search_similar_cases("SyntaxError invalid syntax", module_type="backend")
        assert len(results) >= 1
        assert results[0]["strategy_used"] == "modify_code"

    def test_module_type_boost(self, tmp_auto_fix_cases: Path):
        write_json_cases(
            tmp_auto_fix_cases,
            [
                {
                    "case_id": "fix-0001",
                    "error_keywords": ["syntax", "error"],
                    "fix_summary": "backend fix",
                    "module_type": "backend",
                },
                {
                    "case_id": "fix-0002",
                    "error_keywords": ["syntax", "error"],
                    "fix_summary": "frontend fix",
                    "module_type": "frontend",
                },
            ],
        )
        results = search_similar_cases("syntax error", module_type="backend", top_k=2)
        assert results[0]["module_type"] == "backend"

    def test_format_cases_empty(self):
        assert "无相似" in format_cases_for_prompt([])

    def test_max_200_cases(self, tmp_auto_fix_cases: Path):
        for i in range(205):
            record_fix_case(
                error_text=f"error number {i}",
                module_name=f"mod-{i}",
                module_type="backend",
                fix_summary=f"fix {i}",
                strategy_used="direct_retry",
            )
        data = json.loads(tmp_auto_fix_cases.read_text(encoding="utf-8"))
        assert len(data["cases"]) == 200


class TestFixContext:
    def test_remaining_rounds(self):
        ctx = FixContext(
            module_name="m",
            module_type="backend",
            error_text="err",
            error_type=ErrorType.UNKNOWN,
            total_rounds=3,
        )
        assert ctx.remaining_rounds == 5
        assert ctx.can_continue is True

    def test_record_attempt(self):
        ctx = FixContext(
            module_name="m",
            module_type="backend",
            error_text="err",
            error_type=ErrorType.UNKNOWN,
        )
        ctx.record_attempt("direct_retry", True, "ok")
        assert ctx.total_rounds == 1
        assert ctx.strategies_tried == ["direct_retry"]
        assert ctx.fix_history[0]["success"] is True


class TestFixAsync:
    @pytest.mark.asyncio
    async def test_direct_retry_success(self, tmp_auto_fix_cases):
        logs: list[tuple[str, str]] = []

        async def log_cb(level, message, module_name):
            logs.append((level, message))

        result = await fix(
            "test_mod",
            "backend",
            "read timeout calling API",
            log_callback=log_cb,
        )
        assert result["fixed"] is True
        assert "direct_retry" in result["strategies_tried"]
        assert any("自愈" in msg for _, msg in logs)

    @pytest.mark.asyncio
    async def test_unknown_exhausts_strategies(self):
        result = await fix(
            "test_mod",
            "backend",
            "totally unknown gibberish xyz",
        )
        assert result["fixed"] is False
        assert result["strategies_tried"]
