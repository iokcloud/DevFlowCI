"""模块代码就绪校验 — 在进入 LLM 测试/审查或 pytest 前拦截不完整产出。

避免 JSON 截断、语法未闭合等中间态被误判为「审查不通过」或「测试失败」。
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field


@dataclass
class CodeReadiness:
    """编码产出是否可进入测试/审查阶段。"""

    ready: bool
    phase: str  # empty | truncated | syntax | ok
    issues: list[str] = field(default_factory=list)


_TRUNCATION_PATTERNS = (
    re.compile(r"\.\.\.\s*$"),
    re.compile(r"#\s*TODO(?:\s*:|\s+implement)", re.I),
    re.compile(r"<\s*your\s+code\s+here\s*>", re.I),
    re.compile(r"#\s*placeholder", re.I),
)


def _check_python_syntax(source: str, filename: str = "<module>") -> str | None:
    if not source or not source.strip():
        return "代码为空"
    try:
        compile(source, filename, "exec")
        return None
    except SyntaxError as exc:
        return f"SyntaxError 第 {exc.lineno} 行: {exc.msg}"


def _detect_truncation(source: str) -> list[str]:
    """启发式检测 LLM/JSON 截断导致的不完整代码。"""
    issues: list[str] = []
    if not source or not source.strip():
        return ["代码为空"]

    stripped = source.rstrip()
    if stripped.count('"""') % 2 == 1 or stripped.count("'''") % 2 == 1:
        issues.append("三引号字符串未闭合，疑似输出被截断")

    for open_c, close_c in (("(", ")"), ("[", "]"), ("{", "}")):
        if source.count(open_c) > source.count(close_c):
            issues.append(f"括号 '{open_c}' 未闭合，疑似输出被截断")

    last_line = stripped.split("\n")[-1].strip()
    if last_line.endswith("\\"):
        issues.append("行末续行符未完成，疑似输出被截断")

    for pat in _TRUNCATION_PATTERNS:
        if pat.search(stripped):
            issues.append("包含占位/未完成标记，疑似编码尚未完成")

    # ── 检测"语法正确但逻辑不完整"的截断 ──
    # 场景：LLM 在函数体中段被截断，括号已闭合但函数未写完
    incomplete_ends = frozenset(
        (":", ",", "+", "-", "*", "/", "=", "(", "[", "{", "\\",
         "and", "or", "not", "in", "is", "as", "with", "if",
         "elif", "else", "for", "while", "try", "except", "finally",
         "yield", "return", "assert", "raise", "import", "from")
    )
    if last_line and last_line.split()[-1].rstrip(":") in incomplete_ends:
        issues.append("最后一行代码不完整，疑似输出被截断")

    # 检测 bare comment trail — 如 # ... 或 # (truncated)
    if last_line.startswith("#") and len(last_line) < 20:
        issues.append("以注释结尾且内容过短，疑似占位输出")

    return issues


_DANGLING_LINE_ENDS = (
    "==", "!=", "<=", ">=", "<", ">",
    "+", "-", "*", "/", "%", "//",
    ",", "(", "[", "{",
    " and", " or", " not", " in", " is",
)


def _detect_eof_inside_block(source: str, *, label: str = "代码") -> list[str]:
    """文件在缩进块内结束（常见于 test 写到一半被截断）。"""
    lines = source.splitlines()
    while lines and not lines[-1].strip():
        lines.pop()
    if not lines:
        return []
    last = lines[-1]
    if not (last.startswith(" ") or last.startswith("\t")):
        return []
    stripped = last.strip()
    for tok in _DANGLING_LINE_ENDS:
        if stripped.endswith(tok):
            return [f"{label}在块内末尾语句未完成（…{stripped[-40:]}），疑似截断"]
    if stripped.endswith(":"):
        return [f"{label}末尾以冒号结束、语句体缺失，疑似截断"]
    if stripped.startswith("assert"):
        try:
            compile(f"def _trunc_check():\n{last}\n    pass\n", "<check>", "exec")
        except SyntaxError as exc:
            return [f"{label}末尾 assert 不完整（{exc.msg}），疑似截断"]
    return []


def _detect_incomplete_function_endings(source: str) -> list[str]:
    """AST：函数体最后一行疑似未写完。"""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    lines = source.splitlines()
    issues: list[str] = []
    incomplete_last = frozenset(
        (":", ",", "+", "-", "*", "/", "=", "(", "[", "{",
         "and", "or", "not", "in", "is", "as", "with", "if",
         "elif", "else", "for", "while", "try", "except", "finally",
         "yield", "return", "assert", "raise", "import", "from")
    )
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        if not node.body:
            issues.append(f"函数 {node.name} 函数体为空")
            continue
        end_ln = node.end_lineno
        if not end_ln or end_ln > len(lines):
            continue
        last_in_fn = lines[end_ln - 1].strip()
        if not last_in_fn:
            continue
        tail = last_in_fn.split()[-1].rstrip(":").lower()
        if tail in incomplete_last or last_in_fn.endswith(
            ("==", "!=", ",", "(", "[", "{", " and", " or")
        ):
            issues.append(f"函数 {node.name} 末尾不完整，疑似截断")
    return issues


def assess_module_code(
    code: str,
    test_code: str | None = "",
    *,
    language: str = "python",
    module_description: str = "",
    spec_summary: str = "",
    require_test: bool = True,
) -> CodeReadiness:
    """评估模块代码是否可进入测试/审查。

    Args:
        code: 模块实现代码
        test_code: 单元测试代码（可选但建议一并校验）
        language: 语言标识，目前仅对 python 做语法检查

    Returns:
        CodeReadiness
    """
    issues: list[str] = []

    if not code or not code.strip():
        return CodeReadiness(ready=False, phase="empty", issues=["模块代码为空"])

    if language.lower() == "python":
        trunc = _detect_truncation(code)
        issues.extend(trunc)
        issues.extend(_detect_eof_inside_block(code, label="模块代码"))
        issues.extend(_detect_incomplete_function_endings(code))

        syntax = _check_python_syntax(code)
        if syntax:
            issues.append(syntax)

        if test_code and test_code.strip():
            issues.extend(f"测试: {t}" for t in _detect_truncation(test_code))
            issues.extend(
                f"测试: {t}" for t in _detect_eof_inside_block(test_code, label="测试代码")
            )
            issues.extend(
                f"测试: {t}" for t in _detect_incomplete_function_endings(test_code)
            )
            test_syntax = _check_python_syntax(test_code, "<test>")
            if test_syntax:
                issues.append(f"测试: {test_syntax}")
        elif require_test and test_code is not None and not str(test_code).strip():
            issues.append("测试代码为空")

        if module_description or spec_summary:
            from workflow.code_quick_fix import assess_missing_required_apis

            issues.extend(
                assess_missing_required_apis(
                    code, module_description, spec_summary,
                )
            )

    phase = "ok"
    if issues:
        if any("截断" in i or "占位" in i or "未闭合" in i for i in issues):
            phase = "truncated"
        elif any("SyntaxError" in i or "语法" in i for i in issues):
            phase = "syntax"
        else:
            phase = "syntax"

    return CodeReadiness(ready=not issues, phase=phase, issues=issues)


def format_readiness_feedback(readiness: CodeReadiness) -> str:
    """将就绪问题格式化为编码 Agent 可理解的反馈。"""
    if readiness.ready:
        return ""
    header = {
        "empty": "编码产出为空，请输出完整可运行的模块实现。",
        "truncated": "上次 JSON/代码输出疑似被截断或未写完，请勿进入测试/审查。",
        "syntax": "上次代码存在语法或未闭合结构，请修复后再提交。",
    }.get(readiness.phase, "编码产出尚未就绪，请完善后再提交。")
    detail = "\n".join(f"  - {issue}" for issue in readiness.issues[:8])
    return (
        f"{header}\n具体问题：\n{detail}\n"
        "请输出更精简但语法完整、可 compile 的 JSON 与 Python 代码。\n"
        "测试代码：最多 4 个 test_ 函数，每个函数体必须完整结束（禁止写到 assert 一半）。"
    )
