"""pytest 输出解析与失败反馈。"""

import pytest

from workflow.test_runner import (
    _parse_pytest_output,
    format_test_failure_feedback,
    run_module_tests,
)


def test_parse_collection_import_error():
    output = """
collected 0 items / 1 error
ERROR collecting test_report_dashboard.py
E   ModuleNotFoundError: No module named 'docx'
============================== 1 error in 0.10s ===============================
"""
    result = _parse_pytest_output(output, ["test_x"])
    assert not result.all_passed
    assert result.errors >= 1
    assert "ModuleNotFoundError" in result.summary or result.errors >= 1


def test_all_passed_requires_actual_passes():
    output = "collected 0 items\n"
    result = _parse_pytest_output(output, ["test_x"])
    assert not result.all_passed


def test_format_test_failure_feedback_includes_excerpt():
    data = {
        "summary": "1 通过, 0 失败, 1 错误 (共 1)",
        "execution_output": "E   ModuleNotFoundError: No module named 'docx'",
        "cases": [{"name": "test_x", "passed": False, "error_message": ""}],
    }
    fb = format_test_failure_feedback(data)
    assert "ModuleNotFoundError" in fb


@pytest.mark.asyncio
async def test_import_error_not_reported_as_pass():
    module_code = "from missing_pkg_xyz import foo\n"
    test_code = "from report_dashboard import get_trends\ndef test_x():\n    assert True\n"
    result = await run_module_tests(
        "report_dashboard",
        test_code,
        "parse-test",
        module_code=module_code,
        timeout=20,
    )
    assert not result.all_passed
    assert "✅ 全部 0/1 通过" not in result.summary
