"""code_readiness 单元测试。"""

from workflow.code_readiness import (
    assess_module_code,
    format_readiness_feedback,
)


def test_ready_complete_module():
    code = '''
def add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b
'''
    test = '''
def test_add():
    assert add(1, 2) == 3
'''
    r = assess_module_code(code, test)
    assert r.ready is True
    assert r.phase == "ok"


def test_rejects_truncated_unclosed_paren():
    code = "def foo():\n    return (1 + 2"
    r = assess_module_code(code, "def test_x(): pass")
    assert r.ready is False
    assert r.phase == "truncated"


def test_rejects_syntax_error():
    code = "def foo(\n    return 1"
    r = assess_module_code(code)
    assert r.ready is False
    assert any("SyntaxError" in i for i in r.issues)


def test_rejects_empty_code():
    r = assess_module_code("", "")
    assert r.ready is False
    assert r.phase == "empty"


def test_format_feedback_mentions_truncation():
    r = assess_module_code("def f():\n    x = (1")
    fb = format_readiness_feedback(r)
    assert "截断" in fb or "语法" in fb or "就绪" in fb
