"""Unit tests for scripts/memory_search.py."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import memory_search as ms  # noqa: E402


@pytest.fixture
def tmp_memory_tree(tmp_path: Path, monkeypatch):
    docs = tmp_path / "docs"
    docs.mkdir()
    sessions = tmp_path / "sessions" / "20260601-01"
    sessions.mkdir(parents=True)

    (docs / "LEARNINGS.md").write_text(
        """# LEARNINGS

### 教训 #001：E2E 测试必须有心跳

- **现象**：test_flow 长时间无输出
- **解决方案**：增加心跳与 900s 超时
""",
        encoding="utf-8",
    )
    (docs / "decisions.md").write_text(
        """# decisions

## 决策 #019：E2E 与工作流可观测性加固

- **选择**：心跳 + DB 持久化
""",
        encoding="utf-8",
    )
    (sessions / "summary.md").write_text(
        "# 会话\n\n完成了分支保护配置。\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(ms, "PROJECT_ROOT", tmp_path)
    return tmp_path


def test_search_learnings(tmp_memory_tree):
    hits = ms.search_all("E2E 心跳", sources=["learnings"], top_k=5)
    assert len(hits) >= 1
    assert hits[0].source == "learnings"
    assert "E2E" in hits[0].title or "E2E" in hits[0].excerpt


def test_search_decisions(tmp_memory_tree):
    hits = ms.search_all("可观测性", sources=["decisions"], top_k=5)
    assert len(hits) >= 1
    assert hits[0].source == "decisions"


def test_search_sessions(tmp_memory_tree):
    hits = ms.search_all("分支保护", sources=["sessions"], top_k=5)
    assert len(hits) == 1
    assert hits[0].source == "sessions"


def test_search_no_results(tmp_memory_tree):
    hits = ms.search_all("完全不存在的词汇xyz", sources=["learnings", "decisions"], top_k=5)
    assert hits == []


def test_main_json_output(tmp_memory_tree, capsys):
    code = ms.main(["E2E", "--json", "--source", "learnings"])
    assert code == 0
    data = json.loads(capsys.readouterr().out)
    assert isinstance(data, list)
    assert data[0]["source"] == "learnings"


def test_tokenize_chinese_english():
    tokens = ms.tokenize("API timeout 超时")
    assert "api" in tokens
    assert "timeout" in tokens
