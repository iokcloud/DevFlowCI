"""确定性代码快修 — 在 LLM 修复前处理常见低级错误。

针对 report_dashboard 等解析模块反复出现的：
- 变量拼写错误（descrip → description）
- 仅有内部 _parse_*  helper、缺少对外 get_trends / get_risks
"""

from __future__ import annotations

import ast
import re

_API_FROM_DESC = re.compile(r"\b(get_[a-z_][a-z0-9_]*)\s*\(", re.I)

# helper 名关键词 → 应对外的 API
_HELPER_TO_API: tuple[tuple[tuple[str, ...], str], ...] = (
    (("risk", "md", "markdown"), "get_risks"),
    (("trend", "topic", "docx", "hot"), "get_trends"),
)


def infer_required_apis(description: str, spec_summary: str = "") -> list[str]:
    """从模块描述/规格推断必须存在的顶层函数名。"""
    blob = f"{description}\n{spec_summary}"
    names = _API_FROM_DESC.findall(blob)
    # 保序去重
    seen: set[str] = set()
    out: list[str] = []
    for n in names:
        key = n.lower()
        if key not in seen:
            seen.add(key)
            out.append(n)
    return out


def _top_level_functions(source: str) -> set[str]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    return {
        node.name
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
    }


def _find_helper_for_api(source: str, api_name: str) -> str | None:
    """为缺失的 public API 寻找可委托的内部函数。"""
    funcs = _top_level_functions(source)
    private = [f for f in funcs if f.startswith("_") and not f.startswith("__")]
    if not private:
        return None

    api_lower = api_name.lower()
    if api_lower == "get_risks":
        keys = ("risk", "md", "markdown")
    elif api_lower == "get_trends":
        keys = ("trend", "topic", "docx", "hot")
    else:
        stem = api_lower.replace("get_", "")
        keys = (stem,)

    scored: list[tuple[int, str]] = []
    for fn in private:
        fn_l = fn.lower()
        score = sum(1 for k in keys if k in fn_l)
        if score:
            scored.append((score, fn))
    if not scored:
        return None
    scored.sort(key=lambda x: (-x[0], x[1]))
    return scored[0][1]


def _fix_descrip_typo(source: str) -> tuple[str, bool]:
    """descrip → description（排除已是 description 的情况）。"""
    if not re.search(r"\bdescrip\b(?!tion)", source):
        return source, False
    fixed = re.sub(r"\bdescrip\b(?!tion)", "description", source)
    return fixed, fixed != source


def _wrap_missing_api(source: str, api_name: str, helper: str) -> str:
    wrapper = (
        f"\n\n"
        f"def {api_name}(filepath: str):\n"
        f'    """Public API — delegates to {helper}."""\n'
        f"    return {helper}(filepath)\n"
    )
    return source.rstrip() + wrapper + "\n"


def ensure_public_apis(
    source: str,
    required_apis: list[str],
) -> tuple[str, list[str]]:
    """为缺失的对外 API 生成薄包装（委托已有 _parse_* helper）。"""
    if not source.strip() or not required_apis:
        return source, []

    funcs = _top_level_functions(source)
    notes: list[str] = []
    out = source

    for api in required_apis:
        if api in funcs:
            continue
        helper = _find_helper_for_api(out, api)
        if not helper:
            continue
        out = _wrap_missing_api(out, api, helper)
        funcs.add(api)
        notes.append(f"已自动生成 {api}() 包装 {helper}()")

    return out, notes


def _close_bracket_imbalance(source: str) -> tuple[str, bool]:
    """仅补全末尾缺失的 ) ] }（不修补 assert/语句逻辑）。"""
    if not source.strip():
        return source, False
    stack: list[str] = []
    for ch in source:
        if ch in "([{":
            stack.append(ch)
        elif ch in ")]}":
            if not stack:
                continue
            open_ch = stack[-1]
            expected = ")]}"["([{".index(open_ch)]
            if ch != expected:
                return source, False
            stack.pop()
    if not stack:
        return source, False
    closers = {"(": ")", "[": "]", "{": "}"}
    suffix = "".join(closers[c] for c in reversed(stack))
    return source.rstrip() + suffix + "\n", True


def repair_truncation_shell(code: str, test_code: str = "") -> tuple[str, str, list[str]]:
    """对模块/测试代码做确定性截断壳修复（括号闭合）。"""
    notes: list[str] = []
    out_code, fixed_c = _close_bracket_imbalance(code)
    if fixed_c:
        notes.append("已自动补全模块代码末尾缺失的闭合括号")
    out_test = test_code
    if test_code and test_code.strip():
        out_test, fixed_t = _close_bracket_imbalance(test_code)
        if fixed_t:
            notes.append("已自动补全测试代码末尾缺失的闭合括号")
    return out_code, out_test, notes


def apply_code_quick_fixes(
    code: str,
    *,
    description: str = "",
    spec_summary: str = "",
) -> tuple[str, list[str]]:
    """应用全部确定性快修，返回 (fixed_code, notes)。"""
    if not (code or "").strip():
        return code, []

    notes: list[str] = []
    out = code

    fixed, typo = _fix_descrip_typo(out)
    if typo:
        out = fixed
        notes.append("已修正 descrip → description 拼写错误")

    required = infer_required_apis(description, spec_summary)
    if required:
        out, api_notes = ensure_public_apis(out, required)
        notes.extend(api_notes)

    return out, notes


def assess_missing_required_apis(
    code: str,
    description: str = "",
    spec_summary: str = "",
) -> list[str]:
    """返回仍缺失、且无法自动包装的关键 API 说明。"""
    required = infer_required_apis(description, spec_summary)
    if not required or not (code or "").strip():
        return []

    funcs = _top_level_functions(code)
    issues: list[str] = []
    for api in required:
        if api in funcs:
            continue
        helper = _find_helper_for_api(code, api)
        if helper:
            continue
        issues.append(f"缺少对外接口 {api}()，请实现或委托已有解析函数")
    return issues
