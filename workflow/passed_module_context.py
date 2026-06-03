"""已通过模块 API 摘要 — 注入 analyze/code，避免增量开发「忘记依赖接口」。"""

from __future__ import annotations

import ast
import contextlib
import re
from pathlib import Path
from typing import Any

from config import (
    PASSED_MODULE_API_MAX_CHARS,
    PASSED_MODULE_CONTEXT_ENABLED,
    PASSED_MODULE_CONTEXT_MAX_MODULES,
)
from workflow.iteration_automation import is_preserve_worthy_code

_REUSE_HINT = re.compile(r"复用|已有|依赖|调用", re.IGNORECASE)


def _has_reusable_code(data: dict[str, Any]) -> bool:
    code = (data.get("code") or "").strip()
    if not code:
        return False
    if data.get("status") == "passed":
        return True
    return is_preserve_worthy_code(code, data.get("test_code") or "")


def resolve_relevant_passed_module_names(
    current_module: dict[str, Any],
    memory_passed: set[str],
    module_results: dict[str, dict[str, Any]],
) -> list[str]:
    """选出应注入上下文的已通过/可复用模块名（有序、去重、有上限）。"""
    current_name = (current_module.get("module_name") or "").strip()
    if not current_name:
        return []

    available: set[str] = set(memory_passed or ())
    for name, data in module_results.items():
        if name and name != current_name and _has_reusable_code(data):
            available.add(name)

    if not available:
        return []

    deps = current_module.get("dependencies") or []
    if not isinstance(deps, list):
        deps = []

    selected: list[str] = []
    for dep in deps:
        if isinstance(dep, str) and dep and dep in available and dep not in selected:
            selected.append(dep)

    description = current_module.get("description") or ""
    for name in sorted(available):
        if name in description and name not in selected:
            selected.append(name)

    if not selected and _REUSE_HINT.search(description):
        for name in sorted(available):
            if name not in selected:
                selected.append(name)

    return selected[:PASSED_MODULE_CONTEXT_MAX_MODULES]


def load_passed_module_source(
    module_name: str,
    *,
    directory: str,
    module_results: dict[str, dict[str, Any]],
) -> str:
    """从 module_results 或项目目录读取模块源码。"""
    data = module_results.get(module_name) or {}
    code = (data.get("code") or "").strip()
    if code and _has_reusable_code(data):
        return code

    if directory:
        path = Path(directory) / f"{module_name}.py"
        if path.is_file():
            try:
                return path.read_text(encoding="utf-8", errors="replace").strip()
            except OSError:
                pass
    return ""


def _format_func_args(func: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    try:
        return ast.unparse(func.args)
    except Exception:
        parts: list[str] = []
        for a in func.args.args:
            parts.append(a.arg)
        return ", ".join(parts)


def extract_public_api_summary(code: str) -> str:
    """从源码提取顶层公共 def/class 签名（无函数体）。"""
    code = (code or "").strip()
    if not code:
        return "（无源码）"

    try:
        tree = ast.parse(code)
    except SyntaxError:
        lines = [ln for ln in code.splitlines() if ln.strip()][:35]
        return "\n".join(lines) if lines else "（语法无法解析，见下方片段）"

    lines: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Import | ast.ImportFrom):
            with contextlib.suppress(Exception):
                lines.append(ast.unparse(node))
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            if node.name.startswith("_") and node.name != "__init__":
                continue
            prefix = "async " if isinstance(node, ast.AsyncFunctionDef) else ""
            lines.append(f"{prefix}def {node.name}({_format_func_args(node)}) ...")
        elif isinstance(node, ast.ClassDef):
            if node.name.startswith("_"):
                continue
            lines.append(f"class {node.name}:")
            for item in node.body:
                if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef):
                    if item.name.startswith("_") and item.name != "__init__":
                        continue
                    lines.append(f"    def {item.name}({_format_func_args(item)}) ...")

    if lines:
        return "\n".join(lines)

    compact = "\n".join(ln for ln in code.splitlines() if ln.strip()[:120])
    return compact[:1200] + ("\n# ..." if len(compact) > 1200 else "")


def format_passed_modules_for_prompt(sections: list[tuple[str, str]]) -> str:
    """将 (模块名, API摘要) 列表格式化为 Prompt 块。"""
    if not sections:
        return ""

    blocks: list[str] = [
        "## 可复用的已通过模块（勿重写；新代码须 import 并调用下列公共 API）",
        "签名必须与下列一致；若需扩展行为，封装调用而非复制实现。",
    ]
    for name, summary in sections:
        cap = PASSED_MODULE_API_MAX_CHARS
        body = summary if len(summary) <= cap else summary[:cap] + "\n# ... (API 摘要已截断)"
        blocks.append(f"\n### 模块 `{name}`\n```python\n{body}\n```")
    return "\n".join(blocks)


def build_passed_module_context(
    *,
    current_module: dict[str, Any],
    directory: str,
    module_results: dict[str, dict[str, Any]],
    memory_passed: set[str] | None = None,
    enabled: bool | None = None,
) -> str:
    """为当前待开发模块构建「已通过依赖」上下文文本。"""
    if enabled is None:
        enabled = PASSED_MODULE_CONTEXT_ENABLED
    if not enabled:
        return ""

    names = resolve_relevant_passed_module_names(
        current_module,
        memory_passed or set(),
        module_results,
    )
    if not names:
        return ""

    sections: list[tuple[str, str]] = []
    for name in names:
        source = load_passed_module_source(
            name, directory=directory, module_results=module_results,
        )
        if not source:
            continue
        sections.append((name, extract_public_api_summary(source)))

    return format_passed_modules_for_prompt(sections)
