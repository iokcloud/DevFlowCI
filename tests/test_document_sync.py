"""文档同步服务单元测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from workflow.document_sync import (
    DocumentSyncContext,
    append_changelog_entry,
    load_docs_context_for_agents,
    maybe_append_platform_learning,
    project_docs_dir,
    sync_after_alignment_confirm,
    sync_delivery_plan,
    sync_module_docs,
    sync_product_md,
    write_acceptance_md,
)


@pytest.fixture
def doc_ctx(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "workflow.document_sync.DELIVERIES_DIR",
        tmp_path / "deliveries",
    )
    return DocumentSyncContext(
        project_id="proj-test-001",
        requirement="做一个数据分析看板",
        effective_requirement="做一个数据分析看板",
        iteration=1,
        alignment={
            "summary": "商业数据分析 MVP",
            "assumptions": ["用户有 CSV 数据"],
            "risks": ["数据质量未知"],
            "plan_type": "business",
        },
        plan_modules=[
            {
                "module_name": "report_dashboard",
                "description": "核心看板",
                "type": "backend",
            },
        ],
    )


def test_sync_product_and_plan(doc_ctx):
    sync_after_alignment_confirm(doc_ctx)
    docs = project_docs_dir(doc_ctx.project_id)
    product = (docs / "PRODUCT.md").read_text(encoding="utf-8")
    plan = (docs / "DELIVERY_PLAN.md").read_text(encoding="utf-8")
    assert "数据分析看板" in product
    assert "report_dashboard" in product
    assert "商业数据分析 MVP" in plan
    assert "report_dashboard" in plan


def test_changelog_append(doc_ctx):
    sync_delivery_plan(doc_ctx, event="plan_ready")
    path = append_changelog_entry(
        doc_ctx,
        version_label="v1",
        review={"passed": False, "score": 65, "summary": "需补 dashboard", "issues": ["blocked"]},
        blocked_modules=["report_dashboard"],
    )
    text = path.read_text(encoding="utf-8")
    assert "v1" in text
    assert "65/100" in text
    assert "report_dashboard" in text


def test_load_docs_context_for_agents(doc_ctx, tmp_path, monkeypatch):
    monkeypatch.setattr(
        "workflow.document_sync.DELIVERIES_DIR",
        tmp_path / "deliveries",
    )
    sync_product_md(doc_ctx)
    sync_delivery_plan(doc_ctx)
    ctx_text = load_docs_context_for_agents(doc_ctx.project_id)
    assert "产品说明" in ctx_text
    assert "执行计划" in ctx_text
    assert "勿声称文档未提供" in ctx_text


def test_sync_module_docs(doc_ctx, tmp_path, monkeypatch):
    monkeypatch.setattr(
        "workflow.document_sync.DELIVERIES_DIR",
        tmp_path / "deliveries",
    )
    doc_ctx.module_results = {
        "report_dashboard": {
            "status": "blocked",
            "failure_reason": "测试超时",
            "spec": {"summary": "看板"},
            "test_code": "",
        },
    }
    paths = sync_module_docs(doc_ctx)
    assert len(paths) == 1
    text = paths[0].read_text(encoding="utf-8")
    assert "report_dashboard" in text
    assert "blocked" in text
    assert "测试超时" in text


def test_write_acceptance_md(doc_ctx, tmp_path, monkeypatch):
    monkeypatch.setattr(
        "workflow.document_sync.DELIVERIES_DIR",
        tmp_path / "deliveries",
    )
    doc_ctx.global_review = {"passed": False, "score": 65, "issues": ["dashboard 缺失"]}
    doc_ctx.module_results = {
        "report_dashboard": {"status": "blocked", "failure_reason": "未完成"},
        "auth": {"status": "passed"},
    }
    doc_ctx.blocked_modules = ["report_dashboard"]
    out = write_acceptance_md(doc_ctx, project_docs_dir(doc_ctx.project_id))
    text = out.read_text(encoding="utf-8")
    assert "ACCEPTANCE" in text
    assert "65/100" in text
    assert "report_dashboard" in text
    assert "验收检查清单" in text


def test_maybe_append_platform_learning(doc_ctx, tmp_path, monkeypatch):
    learnings = tmp_path / "LEARNINGS.md"
    learnings.write_text("### 教训 #012：示例\n", encoding="utf-8")
    monkeypatch.setattr("workflow.document_sync.DOCS_DIR", tmp_path)
    doc_ctx.global_review = {"score": 60, "issues": ["低分"]}
    doc_ctx.blocked_modules = ["report_dashboard"]
    result = maybe_append_platform_learning(doc_ctx, trigger="finalize")
    assert result is not None
    appended = learnings.read_text(encoding="utf-8")
    assert "教训 #013" in appended
    assert doc_ctx.project_id in appended
