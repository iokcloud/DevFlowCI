"""已通过模块 API 上下文注入测试。"""

from pathlib import Path

from workflow.passed_module_context import (
    build_passed_module_context,
    extract_public_api_summary,
    format_passed_modules_for_prompt,
    load_passed_module_source,
    resolve_relevant_passed_module_names,
)


def test_extract_public_api_summary_functions_and_classes():
    code = '''
import logging

def get_answer(x: int) -> int:
    return x + 1

class Helper:
    def run(self) -> None:
        pass
'''
    summary = extract_public_api_summary(code)
    assert "def get_answer" in summary
    assert "class Helper" in summary


def test_resolve_names_from_dependencies_and_memory():
    module = {
        "module_name": "web_app",
        "description": "Flask 入口",
        "dependencies": ["llm_client", "missing_mod"],
    }
    names = resolve_relevant_passed_module_names(
        module,
        memory_passed={"llm_client", "prompt_service"},
        module_results={
            "llm_client": {"status": "passed", "code": "def chat(): pass"},
        },
    )
    assert names[0] == "llm_client"


def test_load_from_directory(tmp_path: Path):
    (tmp_path / "llm_client.py").write_text(
        "def chat(msg: str) -> str:\n    return msg\n",
        encoding="utf-8",
    )
    src = load_passed_module_source(
        "llm_client",
        directory=str(tmp_path),
        module_results={},
    )
    assert "def chat" in src


def test_build_context_includes_api_block(tmp_path: Path):
    (tmp_path / "llm_client.py").write_text(
        "def chat(msg: str) -> str:\n    return msg\n",
        encoding="utf-8",
    )
    ctx = build_passed_module_context(
        current_module={
            "module_name": "web_app",
            "description": "复用 llm_client",
            "dependencies": ["llm_client"],
        },
        directory=str(tmp_path),
        module_results={},
        memory_passed={"llm_client"},
    )
    assert "可复用的已通过模块" in ctx
    assert "llm_client" in ctx
    assert "def chat" in ctx


def test_format_empty_returns_empty():
    assert format_passed_modules_for_prompt([]) == ""
