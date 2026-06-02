"""Tests for code_quick_fix."""

from workflow.code_quick_fix import (
    apply_code_quick_fixes,
    assess_missing_required_apis,
    ensure_public_apis,
    infer_required_apis,
)


def test_infer_required_apis_from_description():
    desc = "解析 docx 与 md，对外暴露 get_trends() 与 get_risks() 接口"
    apis = infer_required_apis(desc)
    assert "get_trends" in apis
    assert "get_risks" in apis


def test_fix_descrip_typo():
    code = """
def _parse_risks_md(path: str):
    descrip = match.group(2)
    return Risk(descrip, severity)
"""
    fixed, notes = apply_code_quick_fixes(code)
    assert "descrip =" not in fixed
    assert "Risk(description" in fixed
    assert any("descrip" in n for n in notes)


def test_wrap_missing_get_risks():
    code = """
def _parse_risks_md(filepath: str):
    return [{"description": "x"}]
"""
    desc = "暴露 get_risks() 接口"
    fixed, notes = apply_code_quick_fixes(code, description=desc)
    assert "def get_risks(filepath: str):" in fixed
    assert "_parse_risks_md(filepath)" in fixed
    assert notes


def test_assess_missing_api_when_no_helper():
    code = "def foo():\n    return 1\n"
    issues = assess_missing_required_apis(
        code,
        "需要 get_trends() 和 get_risks()",
    )
    assert len(issues) == 2
    assert all("缺少对外接口" in i for i in issues)


def test_ensure_public_apis_idempotent():
    code = "def get_trends(p): return []\ndef _parse_docx(p): return []\n"
    out, notes = ensure_public_apis(code, ["get_trends"])
    assert out == code
    assert not notes
