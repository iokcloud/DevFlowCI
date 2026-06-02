"""交付文档与项目记忆同步 — 在工作流里程碑更新 PRODUCT / PLAN / CHANGELOG 等。

权威文档目录：deliveries/{project_id}/docs/
每轮打包时复制到 deliveries/{project_id}/vN/ 并追加当轮 CHANGELOG。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

UTC = timezone.utc
from pathlib import Path
from typing import Any

from config import DELIVERIES_DIR, DOCS_DIR
from workflow.requirement_context import (
    build_effective_requirement,
    parse_requirement_addenda,
)

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def project_docs_dir(project_id: str) -> Path:
    """项目级文档目录（跨版本共享）。"""
    path = DELIVERIES_DIR / project_id / "docs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write(path: Path, content: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    except OSError:
        pass


@dataclass
class DocumentSyncContext:
    """文档同步上下文。"""

    project_id: str
    requirement: str = ""
    effective_requirement: str = ""
    iteration: int = 1
    directory: str | None = None
    alignment: dict[str, Any] | None = None
    plan_modules: list[dict[str, Any]] = field(default_factory=list)
    addenda: list[dict[str, str]] = field(default_factory=list)
    module_results: dict[str, dict[str, Any]] = field(default_factory=dict)
    global_review: dict[str, Any] = field(default_factory=dict)
    blocked_modules: list[str] = field(default_factory=list)


def build_ctx_from_workflow_state(state: dict[str, Any]) -> DocumentSyncContext:
    """从工作流 state 构建文档同步上下文。"""
    addenda_raw = state.get("requirement_addendum_json")
    addenda = parse_requirement_addenda(addenda_raw) if addenda_raw else []
    base_req = state.get("base_requirement") or state.get("requirement", "")
    effective = state.get("requirement") or build_effective_requirement(
        base_req, addenda
    )
    return DocumentSyncContext(
        project_id=state["project_id"],
        requirement=base_req,
        effective_requirement=effective,
        iteration=int(state.get("iteration") or 1),
        directory=state.get("directory") or None,
        alignment=state.get("alignment_result") or None,
        plan_modules=state.get("plan_modules") or [],
        addenda=addenda,
        module_results=state.get("module_results") or {},
        global_review=state.get("global_review") or {},
        blocked_modules=list(state.get("blocked_modules") or []),
    )


def ctx_from_project(
    project: Any,
    *,
    plan_modules: list[dict[str, Any]] | None = None,
    alignment: dict[str, Any] | None = None,
) -> DocumentSyncContext:
    """从 ORM Project 构建上下文。"""
    addenda = parse_requirement_addenda(project.requirement_addendum_json)
    return DocumentSyncContext(
        project_id=project.project_id,
        requirement=project.requirement or "",
        effective_requirement=build_effective_requirement(
            project.requirement or "", addenda
        ),
        iteration=project.iteration or 1,
        directory=project.directory,
        alignment=alignment,
        plan_modules=plan_modules or [],
        addenda=addenda,
    )


def sync_product_md(ctx: DocumentSyncContext) -> Path:
    """生成/更新 PRODUCT.md（产品摘要与验收标准）。"""
    docs = project_docs_dir(ctx.project_id)
    alignment = ctx.alignment or {}
    summary = alignment.get("summary") or ctx.requirement[:500] or "待补充"
    plan_type = alignment.get("plan_type", "technical")

    lines = [
        "# 产品说明 (PRODUCT)",
        "",
        f"> 项目: {ctx.project_id}",
        f"> 更新时间: {_now_iso()}",
        f"> 当前迭代轮次: {ctx.iteration}",
        "",
        "## 产品摘要",
        "",
        str(summary),
        "",
        "## 需求来源",
        "",
        ctx.effective_requirement or ctx.requirement or "（无）",
        "",
    ]

    if ctx.addenda:
        lines.append("## 需求补充记录")
        lines.append("")
        for item in ctx.addenda:
            rnd = item.get("round") or "?"
            lines.append(f"### 第 {rnd} 轮")
            lines.append(item.get("text", ""))
            lines.append("")

    assumptions = alignment.get("assumptions") or []
    if assumptions:
        lines.append("## 关键假设")
        lines.append("")
        for a in assumptions:
            lines.append(f"- {a}")
        lines.append("")

    risks = alignment.get("risks") or []
    if risks:
        lines.append("## 已知风险")
        lines.append("")
        for r in risks:
            lines.append(f"- {r}")
        lines.append("")

    if ctx.plan_modules:
        lines.extend([
            "## 功能范围（模块）",
            "",
        ])
        for m in ctx.plan_modules:
            name = m.get("module_name") or m.get("module") or "未命名"
            desc = m.get("description", "")
            lines.append(f"- **{name}**：{desc}")
        lines.append("")

    lines.extend([
        "## 验收标准（建议）",
        "",
        "- 核心模块测试通过，主入口可启动",
        "- README 含安装与运行说明",
        "- 无阻塞模块，或 TODO.md 中阻塞项已明确",
        f"- 全局审查评分 ≥ 70（商业交付建议；当前模式: {plan_type}）",
        "",
    ])

    path = docs / "PRODUCT.md"
    _write(path, "\n".join(lines))
    return path


def sync_delivery_plan(
    ctx: DocumentSyncContext,
    *,
    event: str = "update",
    extra_lines: list[str] | None = None,
) -> Path:
    """生成/更新 DELIVERY_PLAN.md。"""
    docs = project_docs_dir(ctx.project_id)
    alignment = ctx.alignment or {}

    lines = [
        "# 执行计划 (DELIVERY PLAN)",
        "",
        f"> 项目: {ctx.project_id}",
        f"> 事件: {event}",
        f"> 更新时间: {_now_iso()}",
        f"> 迭代轮次: {ctx.iteration}",
        "",
    ]

    if alignment.get("summary"):
        lines.extend(["## 对齐摘要", "", str(alignment["summary"]), ""])

    if ctx.plan_modules:
        lines.extend(["## 模块计划", ""])
        for i, m in enumerate(ctx.plan_modules, 1):
            name = m.get("module_name") or m.get("module") or f"module_{i}"
            lines.append(f"### {i}. {name}")
            lines.append(f"- **描述**: {m.get('description', 'N/A')}")
            lines.append(f"- **类型**: {m.get('type', 'backend')}")
            deps = m.get("dependencies") or []
            if deps:
                lines.append(f"- **依赖**: {', '.join(deps)}")
            lines.append("")

    plan_from_align = alignment.get("plan") or []
    if plan_from_align and not ctx.plan_modules:
        lines.extend(["## 对齐阶段模块（待 PM 细化）", ""])
        for i, m in enumerate(plan_from_align, 1):
            lines.append(f"### {i}. {m.get('module', '未命名')}")
            lines.append(f"- {m.get('description', 'N/A')}")
            lines.append("")

    if ctx.addenda:
        lines.extend(["## 需求补充", ""])
        for item in ctx.addenda:
            lines.append(f"- 第 {item.get('round', '?')} 轮: {item.get('text', '')}")
        lines.append("")

    if extra_lines:
        lines.extend(extra_lines)
        lines.append("")

    path = docs / "DELIVERY_PLAN.md"
    _write(path, "\n".join(lines))
    return path


def sync_iteration_start(
    ctx: DocumentSyncContext,
    *,
    addendum_text: str,
    target_modules: list[str],
    reintegrate_only: bool,
) -> None:
    """迭代启动：更新 PLAN / PRODUCT，追加迭代节。"""
    extra = [
        f"## 迭代启动 — 第 {ctx.iteration} 轮 ({_now_iso()})",
        "",
    ]
    if addendum_text.strip():
        extra.append(f"**本轮补充**: {addendum_text.strip()}")
        extra.append("")
    if reintegrate_only:
        extra.append("**范围**: 仅重新集成与审查（不补跑模块）")
    elif target_modules:
        extra.append(f"**补跑模块**: {', '.join(target_modules)}")
    else:
        extra.append("**范围**: 重新集成交付")
    extra.append("")

    sync_delivery_plan(ctx, event="iteration_start", extra_lines=extra)
    sync_product_md(ctx)


def sync_after_alignment_confirm(ctx: DocumentSyncContext) -> None:
    """对齐确认后：初始化 PRODUCT + DELIVERY_PLAN。"""
    sync_product_md(ctx)
    sync_delivery_plan(ctx, event="alignment_confirmed")


def sync_after_plan_ready(ctx: DocumentSyncContext) -> None:
    """PM 规划完成后：刷新 PLAN + PRODUCT 模块表。"""
    sync_delivery_plan(ctx, event="plan_ready")
    sync_product_md(ctx)


def append_changelog_entry(
    ctx: DocumentSyncContext,
    *,
    version_label: str,
    review: dict[str, Any],
    blocked_modules: list[str],
    target_modules: list[str] | None = None,
) -> Path:
    """追加 CHANGELOG 条目。"""
    docs = project_docs_dir(ctx.project_id)
    path = docs / "CHANGELOG.md"

    score = review.get("score", "?")
    passed = review.get("passed", False)
    summary = review.get("summary", "")[:200]

    entry = [
        "",
        f"## {version_label} — 第 {ctx.iteration} 轮 ({_now_iso()})",
        "",
        f"- **审查结果**: {'通过' if passed else '需改进'}",
        f"- **评分**: {score}/100",
        f"- **摘要**: {summary or '（无）'}",
        f"- **阻塞模块**: {', '.join(blocked_modules) if blocked_modules else '无'}",
    ]
    if target_modules:
        entry.append(f"- **本轮补跑**: {', '.join(target_modules)}")
    issues = review.get("issues") or []
    if issues:
        entry.append("- **主要问题**:")
        for issue in issues[:5]:
            entry.append(f"  - {issue}")
    entry.append("")

    if path.exists():
        existing = path.read_text(encoding="utf-8")
    else:
        existing = (
            "# 变更记录 (CHANGELOG)\n\n"
            f"> 项目: {ctx.project_id}\n\n"
            "记录每轮迭代交付的版本与审查结果。\n"
        )

    _write(path, existing + "\n".join(entry))
    return path


def write_quality_report(
    project_dir: Path,
    ctx: DocumentSyncContext,
    review: dict[str, Any],
) -> Path:
    """写入 QUALITY_REPORT.md（交付包内质量摘要）。"""
    blocked = ctx.blocked_modules
    lines = [
        "# 质量报告 (QUALITY REPORT)",
        "",
        f"> 项目: {ctx.project_id}",
        f"> 迭代轮次: {ctx.iteration}",
        f"> 生成时间: {_now_iso()}",
        "",
        "## 审查结果",
        "",
        f"- **结果**: {'通过' if review.get('passed') else '需改进'}",
        f"- **评分**: {review.get('score', 0)}/100",
        f"- **摘要**: {review.get('summary', '')}",
        "",
    ]

    if blocked:
        lines.extend([
            "## 阻塞模块",
            "",
            *[f"- {name}" for name in blocked],
            "",
        ])

    blocked_issues = review.get("blocked_module_issues") or []
    if blocked_issues:
        lines.extend(["## Blocked 相关问题", ""])
        for item in blocked_issues:
            lines.append(f"- {item}")
        lines.append("")

    issues = review.get("issues") or []
    if issues:
        lines.extend(["## 其他问题", ""])
        for item in issues[:10]:
            lines.append(f"- {item}")
        lines.append("")

    suggestions = review.get("suggestions") or []
    if suggestions:
        lines.extend(["## 改进建议", ""])
        for item in suggestions[:8]:
            lines.append(f"- {item}")
        lines.append("")

    lines.extend([
        "## 参考文档",
        "",
        "- PRODUCT.md — 产品范围与验收标准",
        "- DELIVERY_PLAN.md — 执行计划与迭代记录",
        "- CHANGELOG.md — 版本变更历史",
        "- TODO.md — 待完成项（如有）",
        "",
    ])

    path = project_dir / "QUALITY_REPORT.md"
    _write(path, "\n".join(lines))
    return path


def enhance_todo_with_review(
    project_dir: Path,
    ctx: DocumentSyncContext,
    review: dict[str, Any],
) -> None:
    """在已有 TODO.md 后追加审查待办，或生成仅审查项 TODO。"""
    todo_path = project_dir / "TODO.md"
    review_items = list(review.get("suggestions") or [])[:6]
    if not review_items and not ctx.blocked_modules:
        return

    extra: list[str] = []
    if todo_path.exists():
        extra = ["", "---", "", "## 审查待办（自动生成）", ""]
    else:
        extra = [
            "# 待办清单 (TODO)",
            "",
            f"> 项目: {ctx.project_id}",
            f"> 更新时间: {_now_iso()}",
            "",
        ]

    for i, item in enumerate(review_items, 1):
        extra.append(f"{i}. [审查建议] {item}")
    extra.append("")

    if todo_path.exists():
        _write(todo_path, todo_path.read_text(encoding="utf-8") + "\n".join(extra))
    elif review_items:
        _write(todo_path, "\n".join(extra))


def project_modules_docs_dir(project_id: str) -> Path:
    """模块级文档目录。"""
    path = project_docs_dir(project_id) / "modules"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _module_plan_entry(
    ctx: DocumentSyncContext, module_name: str
) -> dict[str, Any]:
    for m in ctx.plan_modules:
        if (m.get("module_name") or m.get("module")) == module_name:
            return m
    return {}


def sync_module_docs(ctx: DocumentSyncContext) -> list[Path]:
    """为各模块生成 docs/modules/{name}.md。"""
    out: list[Path] = []
    mod_dir = project_modules_docs_dir(ctx.project_id)
    names: set[str] = set()
    for m in ctx.plan_modules:
        n = m.get("module_name") or m.get("module")
        if n:
            names.add(n)
    for n in ctx.module_results:
        names.add(n)

    for name in sorted(names):
        plan = _module_plan_entry(ctx, name)
        result = ctx.module_results.get(name, {})
        status = result.get("status", "pending")
        spec = result.get("spec") or {}
        if isinstance(spec, str):
            try:
                spec = json.loads(spec)
            except (json.JSONDecodeError, TypeError):
                spec = {}

        lines = [
            f"# 模块：{name}",
            "",
            f"> 状态: **{status}**",
            f"> 更新时间: {_now_iso()}",
            "",
            "## 职责",
            "",
            plan.get("description") or spec.get("summary") or "（无描述）",
            "",
        ]

        deps = plan.get("dependencies") or spec.get("dependencies") or []
        if deps:
            lines.extend(["## 依赖", "", *[f"- {d}" for d in deps], ""])

        apis = spec.get("apis") or spec.get("interfaces") or []
        if apis:
            lines.extend(["## 接口", ""])
            if isinstance(apis, list):
                for api in apis[:12]:
                    if isinstance(api, dict):
                        lines.append(
                            f"- `{api.get('name', api.get('path', '?'))}`: "
                            f"{api.get('description', '')}"
                        )
                    else:
                        lines.append(f"- {api}")
            lines.append("")

        test_code = (result.get("test_code") or "").strip()
        test_result = result.get("test_result") or {}
        lines.extend([
            "## 测试",
            "",
            f"- 单元测试: {'已提供' if test_code else '未提供'}",
        ])
        if test_result:
            lines.append(
                f"- 最近结果: {test_result.get('summary', test_result)}"
            )
        lines.append("")

        if status == "blocked":
            lines.extend([
                "## 阻塞原因",
                "",
                result.get("failure_reason") or "（未知）",
                "",
                "## 修复建议",
                "",
                "1. 阅读 PRODUCT.md 中该模块的验收标准",
                "2. 补全实现并重跑单元测试",
                "3. 通过「继续迭代」触发补跑",
                "",
            ])
        elif status == "passed":
            lines.extend([
                "## 实现备注",
                "",
                "- 模块已通过审查与测试门控",
                "",
            ])

        path = mod_dir / f"{name}.md"
        _write(path, "\n".join(lines))
        out.append(path)
    return out


def write_acceptance_md(
    ctx: DocumentSyncContext,
    target_dir: Path,
    *,
    delivery_path: str = "",
) -> Path:
    """写入验收/定稿说明 ACCEPTANCE.md。"""
    review = ctx.global_review or {}
    score = review.get("score", 0)
    passed = review.get("passed", False)
    blocked = ctx.blocked_modules or []

    passed_modules = [
        n for n, r in ctx.module_results.items() if r.get("status") == "passed"
    ]

    lines = [
        "# 验收说明 (ACCEPTANCE)",
        "",
        f"> 项目: {ctx.project_id}",
        f"> 定稿时间: {_now_iso()}",
        f"> 迭代轮次: {ctx.iteration}",
        "",
        "## 交付结论",
        "",
        f"- **审查结果**: {'通过' if passed else '带保留通过' if score >= 50 else '需改进'}",
        f"- **评分**: {score}/100",
        f"- **通过模块**: {len(passed_modules)} 个",
        f"- **阻塞模块**: {len(blocked)} 个",
        "",
    ]

    if delivery_path:
        lines.extend([f"- **交付包**: `{delivery_path}`", ""])

    lines.extend([
        "## 功能清单",
        "",
    ])
    for m in ctx.plan_modules:
        mn = m.get("module_name") or m.get("module") or "?"
        st = ctx.module_results.get(mn, {}).get("status", "pending")
        mark = "✓" if st == "passed" else "!" if st == "blocked" else "○"
        lines.append(f"- [{mark}] **{mn}** ({st}) — {m.get('description', '')[:80]}")
    lines.append("")

    if blocked:
        lines.extend([
            "## 已知限制（未交付/阻塞）",
            "",
            *[f"- **{b}**: {(ctx.module_results.get(b) or {}).get('failure_reason', '待完善')[:120]}"
              for b in blocked],
            "",
        ])

    issues = review.get("issues") or []
    if issues:
        lines.extend(["## 审查遗留项", ""])
        for item in issues[:8]:
            lines.append(f"- {item}")
        lines.append("")

    lines.extend([
        "## 验收检查清单",
        "",
        "- [ ] README 安装步骤可执行",
        "- [ ] 核心 API/功能手动冒烟通过",
        "- [ ] 阻塞模块限制已告知业务方",
        "- [ ] 已知遗留项已记录并接受",
        "",
        "## 运维与文档索引",
        "",
        "- `README.md` — 安装与启动",
        "- `PRODUCT.md` — 产品范围",
        "- `QUALITY_REPORT.md` — 质量审查详情",
        "- `modules/` — 各模块说明（与 docs/modules 同步）",
        "",
    ])

    path = target_dir / "ACCEPTANCE.md"
    _write(path, "\n".join(lines))
    return path


def _next_learning_number(learnings_path: Path) -> int:
    """解析 LEARNINGS.md 中下一可用序号。"""
    if not learnings_path.is_file():
        return 1
    text = learnings_path.read_text(encoding="utf-8")
    nums = [int(n) for n in re.findall(r"教训 #(\d+)", text) if int(n) > 0]
    return max(nums, default=0) + 1


def maybe_append_platform_learning(
    ctx: DocumentSyncContext,
    *,
    trigger: str = "finalize",
) -> Path | None:
    """将可跨项目复用的经验追加到 docs/LEARNINGS.md（仅显著事件）。"""
    review = ctx.global_review or {}
    score = int(review.get("score") or 0)
    blocked = ctx.blocked_modules or []
    should_write = (
        trigger == "finalize"
        or blocked
        or score < 70
        or ctx.iteration > 1
    )
    if not should_write:
        return None

    learnings_path = DOCS_DIR / "LEARNINGS.md"
    if not learnings_path.is_file():
        return None

    num = _next_learning_number(learnings_path)
    title_bits: list[str] = []
    if blocked:
        title_bits.append(f"{len(blocked)} 个模块阻塞")
    if score and score < 70:
        title_bits.append(f"审查 {score} 分")
    if ctx.iteration > 1:
        title_bits.append(f"第 {ctx.iteration} 轮迭代")
    title_suffix = "、".join(title_bits) if title_bits else "交付定稿"

    phenomena: list[str] = []
    if blocked:
        phenomena.append(
            f"项目 {ctx.project_id} 定稿时仍有阻塞模块: {', '.join(blocked)}"
        )
    if score < 70:
        phenomena.append(f"全局审查评分 {score}/100，低于商业交付建议线 70")
    suggestions = review.get("suggestions") or review.get("issues") or []
    if suggestions:
        phenomena.append(f"审查意见: {suggestions[0][:100]}")

    entry = [
        "",
        f"### 教训 #{num:03d}：交付项目 {ctx.project_id} — {title_suffix}",
        "",
        f"- **日期**：{datetime.now(UTC).strftime('%Y-%m-%d')}",
        f"- **来源**：DevFlow 自动文档同步 ({trigger})",
        "- **类别**：协作流程",
        f"- **现象**：{'；'.join(phenomena) if phenomena else '项目完成定稿'}",
        "- **原因**：模块 scope 过大、集成截断或迭代前文档/Product 上下文不足，导致核心模块 blocked 或审查低分。",
        "- **解决方案**：",
        "  1. 对齐阶段锁定 PRODUCT.md 验收标准",
        "  2. 阻塞模块用「继续迭代」增量补跑，避免全量重跑",
        "  3. 打包前同步 CHANGELOG / QUALITY_REPORT / 模块 docs",
        "- **预防措施**：",
        "  - 商业 MVP 优先单核心模块或可集成最小集",
        "  - 每轮迭代必须写需求补充并更新 DELIVERY_PLAN",
        "- **Prompt 改进**：否",
        "",
    ]

    try:
        with open(learnings_path, "a", encoding="utf-8") as f:
            f.write("\n".join(entry))
        return learnings_path
    except OSError:
        return None


def _resolve_latest_version_dir(project_id: str) -> Path | None:
    """解析项目最新版本目录 vN。"""
    base = DELIVERIES_DIR / project_id
    if not base.is_dir():
        return None
    versions = sorted(
        int(d.name[1:])
        for d in base.iterdir()
        if d.is_dir() and d.name.startswith("v") and d.name[1:].isdigit()
    )
    if not versions:
        return None
    return base / f"v{versions[-1]}"


def sync_on_finalize(
    ctx: DocumentSyncContext,
    *,
    delivery_path: str = "",
) -> None:
    """定稿里程碑：ACCEPTANCE + 冻结文档副本 + 平台 LEARNINGS。"""
    docs = project_docs_dir(ctx.project_id)
    sync_module_docs(ctx)
    write_acceptance_md(ctx, docs, delivery_path=delivery_path)

    latest = _resolve_latest_version_dir(ctx.project_id)
    if latest:
        write_acceptance_md(ctx, latest, delivery_path=delivery_path)
        copy_canonical_docs_to_version(ctx.project_id, latest)

    maybe_append_platform_learning(ctx, trigger="finalize")

    acc_src = docs / "ACCEPTANCE.md"
    legacy_acc = DELIVERIES_DIR / ctx.project_id / "ACCEPTANCE.md"
    if acc_src.is_file():
        try:
            legacy_acc.write_text(
                acc_src.read_text(encoding="utf-8"), encoding="utf-8"
            )
        except OSError:
            pass


def copy_canonical_docs_to_version(project_id: str, project_dir: Path) -> None:
    """将 docs/ 下权威文档（含 modules/）复制到当前版本目录。"""
    docs = project_docs_dir(project_id)
    if not docs.is_dir():
        return
    for src in docs.rglob("*.md"):
        try:
            rel = src.relative_to(docs)
            dest = project_dir / rel
            if rel.name == "CHANGELOG.md" and dest.exists():
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        except OSError:
            pass


def load_docs_context_for_agents(project_id: str, *, max_chars: int = 3500) -> str:
    """读取 PRODUCT + DELIVERY_PLAN 摘要，供集成/审查 Agent 使用。"""
    docs = project_docs_dir(project_id)
    parts: list[str] = []

    for name, title in (
        ("PRODUCT.md", "产品说明"),
        ("DELIVERY_PLAN.md", "执行计划"),
    ):
        path = docs / name
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            continue
        if len(text) > max_chars // 2:
            text = text[: max_chars // 2] + "\n... [已截断]"
        parts.append(f"### {title}\n{text}")

    if not parts:
        return ""

    mod_dir = docs / "modules"
    mod_note = ""
    if mod_dir.is_dir():
        count = len(list(mod_dir.glob("*.md")))
        if count:
            mod_note = f"\n\n（另有 {count} 个模块说明见 docs/modules/）"

    return (
        "## 项目文档摘要（请与下列产品/计划保持一致，勿声称文档未提供）\n\n"
        + "\n\n".join(parts)
        + mod_note
    )


def sync_on_package(
    ctx: DocumentSyncContext,
    project_dir: Path,
    *,
    version_num: int,
    project_memory_store: Any | None = None,
) -> None:
    """打包里程碑：CHANGELOG、质量报告、复制文档、更新项目记忆。"""
    version_label = f"v{version_num}"
    review = ctx.global_review or {}

    append_changelog_entry(
        ctx,
        version_label=version_label,
        review=review,
        blocked_modules=ctx.blocked_modules,
    )
    sync_module_docs(ctx)
    write_quality_report(project_dir, ctx, review)
    enhance_todo_with_review(project_dir, ctx, review)
    copy_canonical_docs_to_version(ctx.project_id, project_dir)

    if project_memory_store and ctx.directory:
        try:
            project_memory_store.update_after_delivery(
                directory=ctx.directory,
                requirement=ctx.effective_requirement or ctx.requirement,
                plan=ctx.plan_modules,
                module_results=ctx.module_results,
                last_modified_module=(
                    ctx.plan_modules[-1]["module_name"]
                    if ctx.plan_modules
                    else ""
                ),
            )
        except Exception as exc:
            logger.warning("生成 QA_REPORT 失败: %s", exc)
            pass

    legacy_report = project_dir / "REVIEW_REPORT.md"
    quality = project_dir / "QUALITY_REPORT.md"
    if quality.is_file() and legacy_report.is_file():
        try:
            legacy_report.write_text(
                quality.read_text(encoding="utf-8"), encoding="utf-8"
            )
        except OSError:
            pass
