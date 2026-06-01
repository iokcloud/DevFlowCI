"""Tests for directory document scanning helpers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from workflow.executor import (
    _collect_doc_files,
    _doc_priority_score,
    _read_json_as_text,
    build_context_scan_meta,
)


class TestDocPriority:
    def test_market_report_scores_higher(self):
        assert _doc_priority_score("docs/市场分析报告.md") > _doc_priority_score("misc/notes.txt")

    def test_prompt_library_scores_high(self):
        assert _doc_priority_score("prompts/system_prompt.json") >= _doc_priority_score("other/foo.md")


class TestCollectDocFiles:
    def test_collects_json_and_md(self, tmp_path: Path):
        (tmp_path / "市场分析.md").write_text("市场规模 竞争分析 消费者行为 " * 5, encoding="utf-8")
        (tmp_path / "prompts").mkdir()
        (tmp_path / "prompts" / "lib.json").write_text(
            json.dumps({"role": "analyst", "content": "analyze market"}),
            encoding="utf-8",
        )
        files = _collect_doc_files(tmp_path)
        names = [str(p.relative_to(tmp_path)).replace("\\", "/") for p in files]
        assert "市场分析.md" in names
        assert "prompts/lib.json" in names


class TestReadJsonAsText:
    def test_flattens_nested_json(self, tmp_path: Path):
        fp = tmp_path / "p.json"
        fp.write_text(json.dumps({"a": {"b": "hello world"}}), encoding="utf-8")
        text = _read_json_as_text(fp)
        assert "a.b" in text
        assert "hello world" in text


class TestBuildContextScanMeta:
    def test_meta_shape(self):
        ctx = {
            "analyzed_files": [{"file": "a.md", "summary": "summary text"}],
            "document_type": "business",
            "total_chars_read": 1000,
            "chars_limit": 24000,
            "truncated": False,
            "no_documentation_found": False,
            "project_type": "未知",
            "source_files": [],
        }
        meta = build_context_scan_meta(ctx, directory="/tmp/proj")
        assert meta["file_count"] == 1
        assert meta["document_type"] == "business"
        assert meta["directory"] == "/tmp/proj"
