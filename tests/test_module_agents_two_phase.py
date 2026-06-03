"""ModuleAgents 两轮编码辅助逻辑测试。"""

from workflow.code_readiness import assess_module_code

from agents.module_agents import (
    _extract_prior_code_from_feedback,
    _regen_scope,
)


def test_assess_code_only_skips_empty_test():
    code = "def add(a: int, b: int) -> int:\n    return a + b\n"
    r = assess_module_code(code, test_code=None, require_test=False)
    assert r.ready is True


def test_regen_scope_test_only():
    fb = "【审查修复清单】\n1. [ ] 测试: 缺少异常路径\n\n【现有测试代码】\n```python\ndef test_x(): pass\n```"
    assert _regen_scope(fb) == "test"


def test_regen_scope_both_on_truncation():
    assert _regen_scope("编码产出未就绪（truncated）") == "both"


def test_extract_prior_code_from_feedback():
    fb = """修复清单
【在下列现有代码基础上修改，不要重写为空壳】
```python
def hello():
    return 1
```
"""
    assert "def hello" in _extract_prior_code_from_feedback(fb)
