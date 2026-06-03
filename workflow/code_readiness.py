"""模块代码就绪校验 — 在进入 LLM 测试/审查或 pytest 前拦截不完整产出。

避免 JSON 截断、语法未闭合等中间态被误判为「审查不通过」或「测试失败」。
"""

from __future__ import annotations

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


def assess_module_code(
    code: str,
    test_code: str = "",
    *,
    language: str = "python",
    module_description: str = "",
    spec_summary: str = "",
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

        syntax = _check_python_syntax(code)
        if syntax:
            issues.append(syntax)

        if test_code and test_code.strip():
            issues.extend(f"测试: {t}" for t in _detect_truncation(test_code))
            test_syntax = _check_python_syntax(test_code, "<test>")
            if test_syntax:
                issues.append(f"测试: {test_syntax}")
        elif test_code is not None and not test_code.strip():
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
    return f"{header}\n具体问题：\n{detail}\n请输出更精简但语法完整、可 compile 的 JSON 与 Python 代码。"
