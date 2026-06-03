"""集成产物校验 — main.py 与模块 API 签名对齐。"""

from __future__ import annotations

import ast
import re
from typing import Any


def _required_positional_args(module_source: str, func_name: str) -> int | None:
    """返回函数必需 positional 参数个数（不含 self）。"""
    try:
        tree = ast.parse(module_source)
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            args = node.args
            positional = list(args.args)
            if positional and positional[0].arg == "self":
                positional = positional[1:]
            n_defaults = len(args.defaults)
            return max(0, len(positional) - n_defaults)
    return None


def _main_calls_with_no_args(main_code: str, func_name: str) -> bool:
    try:
        tree = ast.parse(main_code)
    except SyntaxError:
        return bool(re.search(rf"\b{re.escape(func_name)}\(\s*\)", main_code))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == func_name
            and not node.args
            and not node.keywords
        ):
            return True
    return False


def _ensure_fixtures_bootstrap(main_code: str) -> str:
    if "FIXTURES" in main_code:
        return main_code
    lines = main_code.splitlines()
    insert_at = 0
    if "from pathlib import Path" not in main_code:
        lines.insert(0, "from pathlib import Path")
        insert_at = 1
    for i, line in enumerate(lines):
        if line.startswith("from fastapi") or line.startswith("import fastapi"):
            insert_at = max(insert_at, i + 1)
    lines.insert(insert_at, 'FIXTURES = Path(__file__).resolve().parent / "fixtures"')
    return "\n".join(lines) + ("\n" if main_code.endswith("\n") else "")


def _patch_zero_arg_route(main_code: str, func_name: str, glob_pattern: str) -> str:
    line_pat = re.compile(
        rf"^(\s*)return\s+{re.escape(func_name)}\(\s*\)\s*$",
        re.MULTILINE,
    )

    def repl(match: re.Match[str]) -> str:
        indent = match.group(1)
        inner = indent + "    "
        return (
            f"{indent}_path = next(FIXTURES.glob(\"{glob_pattern}\"), None)\n"
            f"{indent}if _path is None:\n"
            f"{inner}return []\n"
            f"{indent}return {func_name}(str(_path))"
        )

    new_out, n = line_pat.subn(repl, main_code)
    if n:
        return new_out
    inline = re.compile(rf"return\s+{re.escape(func_name)}\(\s*\)")
    return inline.sub(
        f"return ({func_name}(str(_p)) if (_p := next(FIXTURES.glob('{glob_pattern}'), None)) else [])",
        main_code,
        count=1,
    )


def validate_and_repair_main_code(
    main_code: str,
    modules_info: list[dict[str, Any]],
) -> tuple[str, list[str]]:
    """校验 main.py 对模块 API 的调用；必要时自动注入 fixtures 路径。"""
    notes: list[str] = []
    if not (main_code or "").strip():
        return main_code, notes

    repaired = main_code
    module_codes = [
        (m.get("code") or "")
        for m in modules_info
        if (m.get("code") or "").strip()
    ]

    for func_name, glob_pat in (("get_trends", "*.docx"), ("get_risks", "*.md")):
        if not _main_calls_with_no_args(repaired, func_name):
            continue
        required_any = any(
            (_required_positional_args(code, func_name) or 0) > 0
            for code in module_codes
        )
        if not required_any:
            continue
        repaired = _ensure_fixtures_bootstrap(repaired)
        repaired = _patch_zero_arg_route(repaired, func_name, glob_pat)
        notes.append(
            f"main.py 中 {func_name}() 缺少路径参数，已改为读取 fixtures/{glob_pat}"
        )

    return repaired, notes
