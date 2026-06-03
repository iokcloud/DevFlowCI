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


def test_detects_incomplete_statement_and():
    """行末以 'and' 结尾：不完整语句。"""
    code = "def foo():\n    return x > 0 and"
    r = assess_module_code(code)
    assert r.ready is False
    assert any("不完整" in i or "截断" in i for i in r.issues)


def test_detects_ends_with_colon():
    """行末以 ':' 结尾：if/for/def 等语句体缺失。"""
    code = "def bar(items):\n    for item in items:\n    pass"
    r = assess_module_code(code)
    assert r.ready is False


def test_detects_ends_with_comma():
    """行末以 ',' 结尾：列表/参数未写完。"""
    code = "def baz():\n    return [1, 2,"
    r = assess_module_code(code)
    assert r.ready is False
    assert any("不完整" in i or "截断" in i for i in r.issues)


def test_valid_code_still_passes():
    """完整函数不应被误报。"""
    code = "def add(a: int, b: int) -> int:\n    return a + b\n"
    r = assess_module_code(code, "def test_add():\n    assert add(1, 2) == 3\n")
    assert r.ready is True
