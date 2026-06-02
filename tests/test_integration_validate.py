"""Tests for integration_validate."""

from workflow.integration_validate import validate_and_repair_main_code


def test_repairs_zero_arg_get_trends_when_module_requires_path():
    main_code = '''
from fastapi import FastAPI
from report_dashboard import get_trends

app = FastAPI()

@app.get("/trends")
async def trends():
    return get_trends()
'''
    modules = [{
        "module_name": "report_dashboard",
        "code": "def get_trends(docx_path: str):\n    return []\n",
    }]
    fixed, notes = validate_and_repair_main_code(main_code, modules)
    assert "FIXTURES" in fixed
    assert 'glob("*.docx")' in fixed
    assert "get_trends(str(_path))" in fixed or "get_trends(str(" in fixed
    assert notes


def test_skips_when_module_has_optional_path():
    main_code = "def route():\n    return get_trends()\n"
    modules = [{
        "module_name": "report_dashboard",
        "code": "def get_trends(docx_path=None):\n    return []\n",
    }]
    fixed, notes = validate_and_repair_main_code(main_code, modules)
    assert fixed == main_code
    assert not notes
