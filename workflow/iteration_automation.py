"""迭代与 blocked 模块自动化：窄 scope 补充、审查清单修复、fixtures、保留末次代码。"""

from __future__ import annotations

import re
import shutil
import zipfile
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from config import DELIVERIES_DIR

STUB_MARKERS = (
    "NotImplementedError",
    "此模块已被自动阻塞",
    "⚠️ 此模块因以下原因被自动阻塞",
    "待实现：",
)

DOCX_FIXTURE_NAME = "热搜话题分析.docx"
MD_FIXTURE_NAME = "交易数据分析.md"


def is_stub_code(code: str) -> bool:
    """是否为 blocked 占位代码。"""
    text = (code or "").strip()
    if not text:
        return True
    return any(marker in text for marker in STUB_MARKERS)


def is_preserve_worthy_code(code: str, test_code: str = "") -> bool:
    """是否值得保留为下一轮修复基础（非占位且有实质实现）。"""
    c = (code or "").strip()
    if not c or is_stub_code(c):
        return False
    if "def " not in c and "class " not in c:
        return False
    # 避免仅骨架函数
    if c.count("\n") < 8 and "pass" in c and c.count("def ") <= 1:
        return False
    return True


def _normalize_issues(issues: list[str] | str | None, failure_reason: str = "") -> list[str]:
    if isinstance(issues, str):
        raw = [issues] if issues.strip() else []
    elif issues:
        raw = [str(x).strip() for x in issues if str(x).strip()]
    else:
        raw = []
    if not raw and failure_reason.strip():
        raw = [
            ln.strip().lstrip("-").strip()
            for ln in failure_reason.splitlines()
            if ln.strip()
        ]
    return raw[:12]


def build_checklist_repair_feedback(
    issues: list[str] | str | None,
    *,
    prior_code: str = "",
    prior_test: str = "",
    failure_reason: str = "",
    module_name: str = "",
) -> str:
    """结构化审查清单 → 编码 Agent 反馈（禁止回退占位）。"""
    items = _normalize_issues(issues, failure_reason)
    if not items:
        items = ["补全实现并通过审查与单元测试"]

    lines = [
        "【审查修复清单 — 必须逐条完成，禁止输出 NotImplementedError 或 blocked 占位】",
    ]
    for i, issue in enumerate(items, 1):
        lines.append(f"{i}. [ ] {issue}")
    lines.append("完成标准：全部勾选项修复后，代码可运行且 pytest 通过。")
    lines.append("禁止：删除已有可运行逻辑、改为占位 stub、省略测试。")

    if module_name:
        lines.append(f"模块：{module_name}")

    if is_preserve_worthy_code(prior_code, prior_test):
        cap = 6000
        code_excerpt = prior_code if len(prior_code) <= cap else prior_code[:cap] + "\n# ... (truncated)"
        lines.append("\n【在下列现有代码基础上修改，不要重写为空壳】\n```python\n" + code_excerpt + "\n```")
        if prior_test.strip():
            tcap = 3000
            test_excerpt = prior_test if len(prior_test) <= tcap else prior_test[:tcap] + "\n# ..."
            lines.append("\n【现有测试代码】\n```python\n" + test_excerpt + "\n```")

    from workflow.review_repair_hints import augment_review_repair_hints

    return augment_review_repair_hints(items, "\n".join(lines))


def build_auto_iterate_addendum(
    module_name: str,
    description: str,
    issues: list[str] | str | None,
    *,
    iteration: int = 0,
    failure_reason: str = "",
) -> str:
    """从 blocked 信息生成窄 scope 迭代补充说明。"""
    item_lines = _normalize_issues(issues, failure_reason)
    issues_block = "\n".join(f"  - {x}" for x in item_lines) if item_lines else "  - 补全实现并通过审查"

    fixture_hints: list[str] = []
    for hint in detect_fixture_needs(description):
        fixture_hints.append(f"fixtures/{hint[1]}")

    fixture_line = ""
    if fixture_hints:
        fixture_line = (
            "\n样例数据（工作流已尝试自动生成，请基于 fixtures/ 解析）：\n"
            + "\n".join(f"  - {p}" for p in fixture_hints)
        )

    return f"""【工作流自动生成 · 第 {iteration} 轮迭代】
仅补跑模块 `{module_name}`，禁止修改其他已通过模块与 main.py 路由结构。
模块目标：{description.strip() or module_name}
必须实现并导出可调用 API（如 get_trends / get_risks），禁止 NotImplementedError 占位。
审查待修复项：
{issues_block}
{fixture_line}
单文件优先，保持与现有测试风格一致；补全 pytest（含空 docx 等边界用例）。
"""


def merge_addenda(user_text: str, auto_text: str) -> str:
    """合并用户补充与工作流自动生成说明。"""
    user = (user_text or "").strip()
    auto = (auto_text or "").strip()
    if user and auto:
        return f"{user}\n\n---\n\n{auto}"
    return user or auto


def detect_fixture_needs(description: str) -> list[tuple[str, str]]:
    """根据模块描述推断需要的 fixtures（扩展名, 文件名）。"""
    desc = description or ""
    needs: list[tuple[str, str]] = []
    if re.search(r"docx|\.docx|Word", desc, re.I):
        needs.append((".docx", DOCX_FIXTURE_NAME))
    if re.search(r"\.md|markdown|md报告|(?:^|\W)md(?:\W|$)", desc, re.I):
        needs.append((".md", MD_FIXTURE_NAME))
    return needs


def _write_minimal_docx(path: Path, paragraph: str) -> None:
    """不依赖 python-docx，写入最小合法 docx。"""
    body = escape(paragraph)
    document_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t>{body}</w:t></w:r></w:p>
  </w:body>
</w:document>"""
    content_types = """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>"""
    rels = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""

    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", rels)
        zf.writestr("word/document.xml", document_xml)


def _copy_fixture_from_directory(
    directory: str,
    fixtures_dir: Path,
    filename: str,
    extensions: tuple[str, ...],
) -> bool:
    """从用户资料目录拷贝匹配样例文件。"""
    root = Path(directory)
    if not root.is_dir():
        return False
    patterns = [
        f"*{ext}" for ext in extensions
    ]
    candidates: list[Path] = []
    for pat in patterns:
        candidates.extend(root.rglob(pat))
    if not candidates:
        return False
    # 优先文件名含关键词
    key = filename.split(".")[0][:4]
    ranked = sorted(
        candidates,
        key=lambda p: (key not in p.name, len(p.name)),
    )
    dest = fixtures_dir / filename
    try:
        shutil.copy2(ranked[0], dest)
        return True
    except OSError:
        return False


def ensure_delivery_fixtures(
    project_id: str,
    module_description: str,
    *,
    directory: str = "",
    structured_context: dict[str, Any] | None = None,
) -> list[str]:
    """在 deliveries/{project_id}/fixtures 准备样例数据，返回创建/复用的相对路径。"""
    fixtures_dir = DELIVERIES_DIR / project_id / "fixtures"
    fixtures_dir.mkdir(parents=True, exist_ok=True)
    created: list[str] = []

    ctx = structured_context or {}
    source_files = ctx.get("source_files") or ctx.get("analyzed_files") or []

    for ext, filename in detect_fixture_needs(module_description):
        rel = f"fixtures/{filename}"
        dest = fixtures_dir / filename
        if dest.is_file():
            created.append(rel)
            continue

        copied = False
        if directory:
            copied = _copy_fixture_from_directory(
                directory, fixtures_dir, filename, (ext,)
            )
        if not copied and source_files:
            for entry in source_files:
                path_str = entry if isinstance(entry, str) else entry.get("path", "")
                if not path_str:
                    continue
                src = Path(path_str)
                if src.is_file() and src.suffix.lower() == ext:
                    try:
                        shutil.copy2(src, dest)
                        copied = True
                        break
                    except OSError:
                        pass

        if not copied:
            if ext == ".docx":
                _write_minimal_docx(
                    dest,
                    "示例话题：小学语文阅读 热度：92\n示例话题：作文批改工具 热度：85",
                )
            elif ext == ".md":
                dest.write_text(
                    "## 避雷提示\n\n- 避免硬广与夸大宣传\n- 注意合规用词\n",
                    encoding="utf-8",
                )
        created.append(rel)

    return created


def infer_extra_requirements(module_description: str, code: str = "") -> list[str]:
    """从描述/代码推断需写入 requirements.txt 的依赖。"""
    deps: list[str] = []
    blob = f"{module_description}\n{code}"
    if re.search(r"docx|python-docx|Document\(", blob, re.I):
        deps.append("python-docx>=1.1.0")
    return deps


def merge_requirements_text(base: str, extra_packages: list[str]) -> str:
    """合并依赖行，避免重复。"""
    lines = [ln.strip() for ln in (base or "").splitlines() if ln.strip()]
    existing = {ln.split("==")[0].split(">=")[0].strip().lower() for ln in lines}
    for pkg in extra_packages:
        name = pkg.split("==")[0].split(">=")[0].strip().lower()
        if name not in existing:
            lines.append(pkg)
            existing.add(name)
    return "\n".join(lines) + ("\n" if lines else "")


_STALE_FAILURE_MARKERS = (
    "截断",
    "未闭合",
    "syntax",
    "SyntaxError",
    "占位",
    "NotImplementedError",
    'logger.warning("N',
    "match 参数未闭合",
)


def refresh_blocked_failure_reason(
    code: str,
    test_code: str,
    review_issues: list[str] | str | None,
    failure_reason: str = "",
) -> str:
    """根据落盘代码刷新 blocked 说明，避免 UI/TODO 展示过期的截断类文案。"""
    if is_stub_code(code):
        text = failure_reason.strip()
        if text:
            return text
        items = _normalize_issues(review_issues, failure_reason)
        return "\n".join(f"  - {x}" for x in items[:8]) if items else "模块未完成（占位）"

    from workflow.code_readiness import assess_module_code

    readiness = assess_module_code(code, test_code or "")
    items = _normalize_issues(review_issues, failure_reason)
    if readiness.ready:
        items = [
            i
            for i in items
            if not any(marker.lower() in i.lower() for marker in _STALE_FAILURE_MARKERS)
        ]

    if readiness.ready and not items:
        if (test_code or "").strip():
            return "审查或单元测试未通过；代码结构已就绪，请继续迭代修复测试/集成"
        return "审查未通过；代码结构已就绪，请补全测试后继续迭代"

    if items:
        return "\n".join(f"  - {x}" for x in items[:8])

    if not readiness.ready and readiness.issues:
        return "\n".join(f"  - {x}" for x in readiness.issues[:8])

    return failure_reason.strip() or "审查或测试未通过"


def resolve_blocked_artifacts(
    *,
    module_name: str,
    module_type: str,
    description: str,
    failure_reason: str,
    code_candidates: list[str],
    test_candidates: list[str],
    stub_generator: Any,
) -> tuple[str, str, bool]:
    """blocked 落盘：优先保留末次可修复代码，仅无可保留代码时生成 stub。

    Returns:
        (code, test_code, used_stub)
    """
    for test_code in test_candidates:
        tc = test_code or ""
        for code in code_candidates:
            if is_preserve_worthy_code(code, tc):
                return code, tc, False

    for code in code_candidates:
        if is_preserve_worthy_code(code, ""):
            return code, "", False

    stub = stub_generator(module_name, module_type, description, failure_reason)
    return stub, "", True


def build_iteration_context_extras(
    *,
    iteration: int,
    target_modules: list[str],
    module_results: dict[str, Any],
    plan_modules: list[dict[str, Any]],
    fixture_paths: list[str],
) -> str:
    """附加到 project_context 的自动化说明。"""
    lines = [f"第 {iteration} 轮迭代（工作流增强模式）。"]
    if fixture_paths:
        lines.append("已准备 fixtures：" + ", ".join(fixture_paths))
    name_to_desc = {
        m["module_name"]: m.get("description", "") for m in plan_modules
    }
    for name in target_modules:
        prior = module_results.get(name, {})
        if is_preserve_worthy_code(prior.get("code", ""), prior.get("test_code", "")):
            lines.append(f"- {name}：保留上轮代码，按审查清单修复")
        elif is_stub_code(prior.get("code", "")):
            lines.append(f"- {name}：上轮为占位，需完整实现 — {name_to_desc.get(name, '')[:80]}")
    return "\n".join(lines)
