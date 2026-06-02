"""审查问题 → 针对性修复提示，提升 modify_code / 自愈命中率。"""

from __future__ import annotations

# (关键词元组, 修复提示)
_REPAIR_HINT_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (
        ("静默", "空列表", "掩盖", "无法感知", "解析失败"),
        (
            "异常处理：仅「文件合法但无匹配行」可 return []。"
            "文件损坏/IO/Document 打开失败须 raise RuntimeError；"
            "后缀/路径错误用 ValueError/FileNotFoundError。"
            "禁止 except Exception: return []。"
            "若存在表头却 0 条有效行，raise ValueError('无法解析')。"
        ),
    ),
    (
        ("数值范围", "rank", "popularity", "score", "负数", "超大", "校验", "安全"),
        (
            "安全/校验：集中 _validated_trend(rank, topic, score)，"
            "rank∈[1,10000]，score∈[0,1e9]；越界 raise ValueError。"
            "表格逐行 try/except ValueError 后 logger.warning 跳过坏行。"
            "补 pytest：负 rank 被跳过/拒绝、损坏 docx 抛 RuntimeError。"
        ),
    ),
    (
        ("docstring", "raises", "后缀校验", "后缀", "未完整闭合"),
        (
            "质量：公共 API 须有完整 docstring（含 Raises）；"
            "get_trends 与 get_risks 后缀校验规则一致（.docx / .md）。"
        ),
    ),
    (
        ("测试", "pytest", "边界", "空文件"),
        (
            "测试：保留现有用例；补充空文件返回 []、损坏文件抛异常、"
            "越界数值至少 1 个用例；勿删 fixtures 相关测试。"
        ),
    ),
    (
        ("缺少", "接口", "get_trends", "get_risks", "对外", "暴露"),
        (
            "接口：必须在模块顶层定义 get_trends(filepath) 与 get_risks(filepath)；"
            "若已有 _parse_* 内部函数，写薄包装 return _parse_xxx(filepath)，"
            "禁止仅实现私有 helper 而不导出 public API。"
        ),
    ),
    (
        ("descrip", "拼写", "typo", "变量名", "nameerror", "未定义"),
        (
            "拼写：将所有 descrip 统一改为 description；"
            "Risk 字段、正则 group、局部变量命名保持一致。"
        ),
    ),
)


def augment_review_repair_hints(
    issues: list[str] | str | None,
    feedback: str,
) -> str:
    """在审查清单反馈后追加可执行的窄 scope 修复提示。"""
    if isinstance(issues, str):
        blob = issues
    elif issues:
        blob = " ".join(str(x) for x in issues)
    else:
        blob = feedback
    blob_lower = blob.lower()

    hints: list[str] = []
    for keywords, hint in _REPAIR_HINT_RULES:
        if any(k.lower() in blob_lower for k in keywords):
            hints.append(f"- {hint}")

    if not hints:
        return feedback

    block = "\n".join(hints)
    return (
        f"{feedback.rstrip()}\n\n"
        "【工作流针对性修复提示 — 在现有代码上增量修改，勿整文件重写】\n"
        f"{block}"
    )
