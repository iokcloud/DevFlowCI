"""Unit tests for memory.case_store."""

from __future__ import annotations

from pathlib import Path

from memory.case_store import CaseStore, SuccessCase, TfidfRetriever


def _sample_plan(name: str = "auth") -> list[dict]:
    return [{"module_name": name, "description": f"{name} module"}]


def _sample_modules(name: str = "auth") -> list[dict]:
    return [{"module_name": name, "module_type": "backend", "description": "auth api"}]


class TestSuccessCase:
    def test_roundtrip(self):
        case = SuccessCase(
            case_id="case-0001",
            requirement="用户登录系统",
            plan=_sample_plan(),
            modules=_sample_modules(),
            created_at="2026-06-01T00:00:00+00:00",
            keywords=["用户", "登录"],
        )
        restored = SuccessCase.from_dict(case.to_dict())
        assert restored.case_id == case.case_id
        assert restored.requirement == case.requirement
        assert restored.keywords == case.keywords


class TestTfidfRetriever:
    def test_search_similar_requirement(self):
        cases = [
            SuccessCase(
                case_id="case-0001",
                requirement="实现用户登录和注册 API",
                plan=[],
                modules=[],
                created_at="",
                keywords=["用户", "登录"],
            ),
            SuccessCase(
                case_id="case-0002",
                requirement="天气查询小程序前端页面",
                plan=[],
                modules=[],
                created_at="",
                keywords=["天气"],
            ),
        ]
        retriever = TfidfRetriever(cases)
        results = retriever.search("用户登录功能", top_k=1)
        assert len(results) == 1
        assert results[0][0].case_id == "case-0001"
        assert results[0][1] > 0

    def test_search_empty(self):
        retriever = TfidfRetriever([])
        assert retriever.search("anything") == []


class TestCaseStore:
    def test_add_and_search(self, tmp_success_cases: Path):
        store = CaseStore(tmp_success_cases)
        store.add_success(
            requirement="REST API 用户认证模块",
            plan=_sample_plan("auth"),
            modules=_sample_modules("auth"),
        )
        assert store.count == 1
        results = store.search_similar("用户认证 API")
        assert len(results) >= 1
        assert results[0]["case_id"].startswith("case-")

    def test_format_few_shot_no_cases(self, tmp_success_cases: Path):
        store = CaseStore(tmp_success_cases)
        text = store.format_few_shot("任意需求")
        assert "无相似" in text

    def test_format_few_shot_with_cases(self, tmp_success_cases: Path):
        store = CaseStore(tmp_success_cases)
        store.add_success(
            requirement="博客文章 CRUD",
            plan=_sample_plan("blog"),
            modules=_sample_modules("blog"),
        )
        text = store.format_few_shot("博客系统")
        assert "参考案例" in text
        assert "博客" in text

    def test_eviction_at_max(self, tmp_success_cases: Path, monkeypatch):
        monkeypatch.setattr("memory.case_store.MAX_SUCCESS_CASES", 3)
        store = CaseStore(tmp_success_cases)
        for i in range(5):
            store.add_success(
                requirement=f"需求 {i}",
                plan=_sample_plan(f"mod{i}"),
                modules=_sample_modules(f"mod{i}"),
            )
        assert store.count == 3
        assert store.cases[0].requirement == "需求 2"

    def test_load_corrupt_json(self, tmp_success_cases: Path):
        tmp_success_cases.write_text("{not valid json", encoding="utf-8")
        store = CaseStore(tmp_success_cases)
        assert store.count == 0
