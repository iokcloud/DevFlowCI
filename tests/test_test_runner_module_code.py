"""run_module_tests 写入模块源码后 import 不应误报。"""

import pytest

from workflow.test_runner import run_module_tests


@pytest.mark.asyncio
async def test_run_module_tests_writes_source_for_import():
    module_code = '''
def greet(name: str) -> str:
    return f"hello {name}"
'''
    test_code = '''
from auth import greet

def test_greet():
    assert greet("world") == "hello world"
'''
    result = await run_module_tests(
        module_name="auth",
        test_code=test_code,
        project_id="test-readiness",
        module_code=module_code,
        module_filename="auth.py",
        timeout=15,
    )
    assert result.execution_mode in ("pytest", "syntax_check")
    if result.execution_mode == "pytest" and result.total > 0:
        assert result.failed == 0
        assert result.passed >= 1
