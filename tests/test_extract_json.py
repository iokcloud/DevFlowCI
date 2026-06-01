"""Tests for utils.extract_json."""

from __future__ import annotations

import pytest

from utils import extract_json


def test_extract_json_direct():
    assert extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_codeblock():
    text = '说明\n```json\n{"module_name": "x", "code": "print(1)"}\n```'
    data = extract_json(text)
    assert data["module_name"] == "x"


def test_extract_json_truncated_codeblock():
    """截断的 coder JSON 应能补全闭合。"""
    text = """```json
{
  "module_name": "demo",
  "language": "python",
  "code": "def hello():\\n    return 1",
  "test_code": "def test_hello():\\n    assert hello() == 1"
"""
    data = extract_json(text)
    assert data["module_name"] == "demo"
    assert "def hello" in data["code"]


def test_extract_json_invalid_raises():
    with pytest.raises(ValueError, match="无法从回复中提取有效 JSON"):
        extract_json("not json at all")
