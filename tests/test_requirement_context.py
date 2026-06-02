"""需求合并逻辑测试。"""

from __future__ import annotations

from workflow.requirement_context import (
    has_directory_context,
    merge_sources_label,
    persist_requirement_text,
    user_instruction,
)


def test_persist_user_text_priority():
    assert persist_requirement_text("做待办 CLI", "/tmp/proj") == "做待办 CLI"


def test_persist_directory_only():
    text = persist_requirement_text("", "D:/data/market-reports")
    assert "market-reports" in text
    assert user_instruction("") == ""


def test_merge_sources_label():
    assert merge_sources_label("todo", "/x") == "文字指令 + 目录资料"
    assert merge_sources_label("", "/x") == "目录资料"
    assert merge_sources_label("todo", "") == "文字指令"


def test_has_directory_context_source_files():
    assert has_directory_context({"source_files": ["app.py"], "no_documentation_found": True})
