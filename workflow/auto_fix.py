"""异常自愈模块 — 错误分类、修复策略调度与案例库管理。

提供：
1. classify_error(): 从错误信息中识别错误类型
2. search_similar_cases(): Jaccard 相似度检索历史修复案例
3. record_fix_case(): 修复成功后记录到案例库
4. fix(): 根据错误类型执行修复策略
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

UTC = timezone.utc
from typing import Any

from config import (
    AUTO_FIX_CASE_TOP_K,
    AUTO_FIX_CASES_FILE,
    AUTO_FIX_RETRY_BACKOFF,
    AUTO_FIX_RETRY_BASE_DELAY,
    AUTO_FIX_RETRY_MAX_DELAY,
    ERROR_FIX_STRATEGY_MAP,
    ErrorType,
)

logger = logging.getLogger(__name__)

# ── 错误分类 ──────────────────────────────────────────────

# 错误特征关键词 → ErrorType 映射（⚠️ 长模式优先，避免短模式误匹配）
_ERROR_PATTERNS: dict[str, ErrorType] = {
    # 测试执行失败（运行时崩溃/超时） — 必须在 "timeout" 之前
    "test execution failure": ErrorType.TEST_EXECUTION_FAILURE,
    "test execution timeout": ErrorType.TEST_EXECUTION_FAILURE,
    "test execution error": ErrorType.TEST_EXECUTION_FAILURE,
    "error collecting": ErrorType.TEST_EXECUTION_FAILURE,
    "no tests ran": ErrorType.TEST_EXECUTION_FAILURE,
    "测试执行异常": ErrorType.TEST_EXECUTION_FAILURE,
    "测试执行超时": ErrorType.TEST_EXECUTION_FAILURE,
    # API 超时
    "read timeout": ErrorType.API_TIMEOUT,
    "connect timeout": ErrorType.API_TIMEOUT,
    "timed out": ErrorType.API_TIMEOUT,
    "timeout": ErrorType.API_TIMEOUT,
    "timedout": ErrorType.API_TIMEOUT,
    # API 速率限制
    "rate limit": ErrorType.API_RATE_LIMIT,
    "too many requests": ErrorType.API_RATE_LIMIT,
    "429": ErrorType.API_RATE_LIMIT,
    "quota exceeded": ErrorType.API_RATE_LIMIT,
    "throttle": ErrorType.API_RATE_LIMIT,
    # 语法错误
    "syntax error": ErrorType.SYNTAX_ERROR,
    "syntaxerror": ErrorType.SYNTAX_ERROR,
    "indentationerror": ErrorType.SYNTAX_ERROR,
    "taberror": ErrorType.SYNTAX_ERROR,
    "invalid syntax": ErrorType.SYNTAX_ERROR,
    "unexpected eof": ErrorType.SYNTAX_ERROR,
    "expected colon": ErrorType.SYNTAX_ERROR,
    # 测试失败（审查/LLM 评估层面）
    "assertionerror": ErrorType.TEST_FAILURE,
    "assertion error": ErrorType.TEST_FAILURE,
    "assertionerror:": ErrorType.TEST_FAILURE,
    "test failed": ErrorType.TEST_FAILURE,
    "tests failed": ErrorType.TEST_FAILURE,
    "failed:": ErrorType.TEST_FAILURE,  # pytest 输出
    "!= ": ErrorType.TEST_FAILURE,      # pytest assert
    # 审查失败
    "fail:": ErrorType.REVIEW_FAIL,
    "代码审查不通过": ErrorType.REVIEW_FAIL,
    "审查不通过": ErrorType.REVIEW_FAIL,
    "缺少": ErrorType.REVIEW_FAIL,
    "review: fail": ErrorType.REVIEW_FAIL,
    # 依赖缺失
    "modulenotfounderror": ErrorType.DEPENDENCY_MISSING,
    "importerror": ErrorType.DEPENDENCY_MISSING,
    "no module named": ErrorType.DEPENDENCY_MISSING,
    "cannot import": ErrorType.DEPENDENCY_MISSING,
    "could not be resolved": ErrorType.DEPENDENCY_MISSING,
    "dependency not found": ErrorType.DEPENDENCY_MISSING,
}


def classify_error(error_text: str) -> ErrorType:
    """从错误文本中识别可自动修复的错误类型。

    Args:
        error_text: 错误消息或堆栈信息

    Returns:
        ErrorType 枚举值
    """
    text_lower = error_text.lower()
    for pattern, error_type in _ERROR_PATTERNS.items():
        if pattern in text_lower:
            return error_type
    return ErrorType.UNKNOWN


# ── 指数退避 ──────────────────────────────────────────────

async def backoff_delay(attempt: int) -> float:
    """计算并等待指数退避延迟。

    Args:
        attempt: 当前尝试次数（从 1 开始）

    Returns:
        实际等待的秒数
    """
    delay = min(
        AUTO_FIX_RETRY_BASE_DELAY * (AUTO_FIX_RETRY_BACKOFF ** (attempt - 1)),
        AUTO_FIX_RETRY_MAX_DELAY,
    )
    # 加入 10% 随机抖动
    jitter = delay * 0.1 * (hash(str(attempt)) % 10) / 10
    actual = delay + jitter
    await asyncio.sleep(actual)
    return actual


# ── Jaccard 相似度检索 ────────────────────────────────────

def _tokenize(text: str) -> set[str]:
    """分词：小写化 + 中英文混合 token 提取。

    Args:
        text: 输入文本

    Returns:
        小写 token 集合
    """
    tokens: set[str] = set()
    # 英文单词（2 个字母以上）
    for match in re.finditer(r"[a-zA-Z_]{2,}", text.lower()):
        tokens.add(match.group())
    # 中文 bigram
    chinese = re.findall(r"[\u4e00-\u9fff]+", text)
    for seg in chinese:
        for i in range(len(seg) - 1):
            tokens.add(seg[i: i + 2])
        tokens.update(set(seg))
    return tokens


def jaccard_similarity(set_a: set[str], set_b: set[str]) -> float:
    """计算两个集合的 Jaccard 相似度。

    Args:
        set_a: 第一个 token 集合
        set_b: 第二个 token 集合

    Returns:
        0.0 ~ 1.0 的相似度
    """
    if not set_a or not set_b:
        return 0.0
    intersection = set_a & set_b
    union = set_a | set_b
    return len(intersection) / len(union)


def _load_cases() -> list[dict[str, Any]]:
    """从文件加载自动修复案例。

    Returns:
        案例字典列表
    """
    file_path = AUTO_FIX_CASES_FILE
    if not file_path.exists():
        return []
    try:
        data = json.loads(file_path.read_text(encoding="utf-8"))
        return data.get("cases", [])
    except (json.JSONDecodeError, KeyError):
        return []


def _save_cases(cases: list[dict[str, Any]]) -> None:
    """持久化案例到文件。

    Args:
        cases: 案例字典列表
    """
    file_path = AUTO_FIX_CASES_FILE
    file_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": "1.0.0",
        "last_updated": datetime.now(UTC).isoformat(),
        "cases": cases,
    }
    file_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def search_similar_cases(
    error_text: str,
    module_type: str = "",
    top_k: int = AUTO_FIX_CASE_TOP_K,
) -> list[dict[str, Any]]:
    """检索与当前错误最相似的历史修复案例。

    使用 Jaccard 相似度（关键词重叠）计算相关性。

    Args:
        error_text: 错误消息文本
        module_type: 模块类型（用于加权匹配）
        top_k: 返回案例数

    Returns:
        相似度排序后的案例列表
    """
    cases = _load_cases()
    if not cases:
        return []

    query_tokens = _tokenize(error_text)

    scored: list[tuple[dict[str, Any], float]] = []
    for case in cases:
        # 合并案例的错误特征和修复摘要进行匹配
        case_text = " ".join(case.get("error_keywords", [])) + " " + case.get("fix_summary", "")
        case_tokens = _tokenize(case_text)
        sim = jaccard_similarity(query_tokens, case_tokens)

        # 同类型模块加权
        if module_type and case.get("module_type", "") == module_type:
            sim *= 1.2

        if sim > 0.01:
            scored.append((case, sim))

    scored.sort(key=lambda x: x[1], reverse=True)
    return [case for case, _ in scored[:top_k]]


def record_fix_case(
    error_text: str,
    module_name: str,
    module_type: str,
    fix_summary: str,
    strategy_used: str,
) -> None:
    """记录一次成功的自动修复案例。

    Args:
        error_text: 原始错误消息
        module_name: 模块名称
        module_type: 模块类型
        fix_summary: 修复方法摘要
        strategy_used: 使用的修复策略标识
    """
    cases = _load_cases()

    # 提取错误关键词
    error_keywords = list(_tokenize(error_text))[:20]

    case = {
        "case_id": f"fix-{len(cases) + 1:04d}",
        "error_keywords": error_keywords,
        "error_preview": error_text[:200],
        "module_name": module_name,
        "module_type": module_type,
        "fix_summary": fix_summary,
        "strategy_used": strategy_used,
        "created_at": datetime.now(UTC).isoformat(),
    }

    # 保持最大 200 条案例
    if len(cases) >= 200:
        cases.pop(0)

    cases.append(case)
    _save_cases(cases)


def format_cases_for_prompt(cases: list[dict[str, Any]]) -> str:
    """将历史修复案例格式化为可注入 Prompt 的文本。

    Args:
        cases: 案例列表

    Returns:
        格式化的参考文本
    """
    if not cases:
        return "（无相似历史修复案例）"

    lines: list[str] = [
        f"以下是 {len(cases)} 个相似的历史自动修复案例，供参考：\n"
    ]
    for i, case in enumerate(cases, 1):
        lines.append(f"### 修复案例 {i}")
        lines.append(f"- 错误特征: {', '.join(case.get('error_keywords', [])[:10])}")
        lines.append(f"- 模块类型: {case.get('module_type', 'unknown')}")
        lines.append(f"- 修复方法: {case.get('fix_summary', '')}")
        lines.append(f"- 使用策略: {case.get('strategy_used', '')}")
        lines.append("")
    return "\n".join(lines)


# ── 策略调度 ──────────────────────────────────────────────

@dataclass
class FixContext:
    """修复上下文，在修复流程中传递。"""

    module_name: str
    module_type: str
    error_text: str
    error_type: ErrorType
    spec: dict[str, Any] = field(default_factory=dict)
    code: str = ""
    test_code: str = ""
    review_issues: list[str] = field(default_factory=list)

    # 统计
    total_rounds: int = 0
    strategies_tried: list[str] = field(default_factory=list)
    fix_history: list[dict[str, Any]] = field(default_factory=list)

    @property
    def remaining_rounds(self) -> int:
        from config import AUTO_FIX_MAX_TOTAL_ROUNDS
        return max(0, AUTO_FIX_MAX_TOTAL_ROUNDS - self.total_rounds)

    @property
    def can_continue(self) -> bool:
        return self.remaining_rounds > 0

    def record_attempt(self, strategy: str, success: bool, detail: str = "") -> None:
        """记录一次修复尝试。"""
        self.total_rounds += 1
        self.strategies_tried.append(strategy)
        self.fix_history.append({
            "round": self.total_rounds,
            "strategy": strategy,
            "success": success,
            "detail": detail,
            "timestamp": datetime.now(UTC).isoformat(),
        })


async def fix(
    module_name: str,
    module_type: str,
    error_info: str,
    context: dict[str, Any] | None = None,
    *,
    log_callback=None,
    coder_callback=None,
    reviewer_callback=None,
    repair_agent_callback=None,
) -> dict[str, Any]:
    """执行异常自愈修复。

    根据错误类型依次尝试对应策略，直到修复成功或所有策略耗尽。

    Args:
        module_name: 模块名称
        module_type: 模块类型
        error_info: 错误信息文本
        context: 额外上下文（spec、code、test_code、review_issues 等）
        log_callback: 日志回调 async fn(level, message, module_name)
        coder_callback: 编码回调 async fn(module_name, spec, feedback) -> ModuleCode
        reviewer_callback: 审查回调 async fn(module_name, spec_summary, code, test_code, retry_count) -> ReviewResult
        repair_agent_callback: RepairAgent 回调 async fn(module_output, history_cases) -> dict

    Returns:
        {
            "fixed": bool,
            "code": str | None,
            "test_code": str | None,
            "strategies_tried": [...],
            "fix_history": [...],
            "final_error": str,
        }
    """
    ctx = context or {}
    error_type = classify_error(error_info)

    fix_ctx = FixContext(
        module_name=module_name,
        module_type=module_type,
        error_text=error_info,
        error_type=error_type,
        spec=ctx.get("spec", {}),
        code=ctx.get("code", ""),
        test_code=ctx.get("test_code", ""),
        review_issues=ctx.get("review_issues", []),
        total_rounds=ctx.get("total_rounds", 0),
    )

    async def log(level: str, message: str) -> None:
        if log_callback:
            await log_callback(level, message, module_name)

    await log("INFO", f"[自愈] 检测到错误类型: {error_type.value}")

    # 获取该错误类型的策略列表
    strategies = ERROR_FIX_STRATEGY_MAP.get(error_type.value, ERROR_FIX_STRATEGY_MAP["unknown"])

    for strategy in strategies:
        if not fix_ctx.can_continue:
            await log("WARN", f"[自愈] 已达最大修复轮次({fix_ctx.total_rounds})，停止自愈")
            break

        await log("INFO", f"[自愈] 尝试策略: {strategy}（第 {fix_ctx.total_rounds + 1} 轮）")

        try:
            if strategy == "direct_retry":
                result = await _try_direct_retry(fix_ctx, log)
            elif strategy == "modify_code":
                result = await _try_modify_code(fix_ctx, log, coder_callback, reviewer_callback)
            elif strategy == "repair_agent":
                result = await _try_repair_agent(fix_ctx, log, repair_agent_callback, reviewer_callback)
            elif strategy == "history_case":
                result = await _try_history_case(fix_ctx, log, repair_agent_callback, reviewer_callback)
            elif strategy == "block_with_stub":
                result = {"fixed": False, "reason": "所有策略已尝试，进入阻塞流程"}
                fix_ctx.record_attempt(strategy, False, result["reason"])
            else:
                continue

            if result.get("fixed"):
                await log("SUCCESS", f"[自愈] 策略 '{strategy}' 修复成功！")
                # 记录成功案例
                try:
                    fix_summary = result.get("fix_summary", f"使用 {strategy} 修复")
                    record_fix_case(
                        error_text=error_info,
                        module_name=module_name,
                        module_type=module_type,
                        fix_summary=fix_summary,
                        strategy_used=strategy,
                    )
                except Exception as exc:
                    logger.warning("记录修复案例失败: %s", exc)
                    pass
                return {
                    "fixed": True,
                    "code": result.get("code"),
                    "test_code": result.get("test_code"),
                    "strategies_tried": fix_ctx.strategies_tried,
                    "fix_history": fix_ctx.fix_history,
                    "final_error": "",
                }

        except Exception as exc:
            await log("ERROR", f"[自愈] 策略 '{strategy}' 执行异常: {exc}")
            fix_ctx.record_attempt(strategy, False, str(exc))

    return {
        "fixed": False,
        "code": None,
        "test_code": None,
        "strategies_tried": fix_ctx.strategies_tried,
        "fix_history": fix_ctx.fix_history,
        "final_error": f"自动修复失败，已尝试策略: {', '.join(fix_ctx.strategies_tried)}",
    }


# ── 策略实现 ──────────────────────────────────────────────

async def _try_direct_retry(
    fix_ctx: FixContext,
    log,
) -> dict[str, Any]:
    """策略 a)：带指数退避的直接重试。

    Returns:
        {"fixed": bool, "reason": str}
    """
    delay = await backoff_delay(fix_ctx.total_rounds + 1)
    await log("INFO", f"[自愈·退避重试] 等待 {delay:.1f}s 后重试...")
    fix_ctx.record_attempt(
        "direct_retry", True,
        f"退避等待 {delay:.1f}s，允许调用方重试",
    )
    # 直接重试只是延迟等待，实际重试由调用方执行
    return {"fixed": True, "fix_summary": f"指数退避等待 {delay:.1f}s 后重试"}


async def _try_modify_code(
    fix_ctx: FixContext,
    log,
    coder_callback,
    reviewer_callback,
) -> dict[str, Any]:
    """策略 b)：将错误信息注入编码 Agent，重新生成代码。

    Returns:
        {"fixed": bool, "code": str, "test_code": str, "fix_summary": str}
    """
    if not coder_callback:
        return {"fixed": False, "reason": "缺少编码 Agent 回调"}

    await log("INFO", "[自愈·修改代码] 将错误反馈发给编码 Agent 修正...")
    fix_ctx.record_attempt("modify_code", False, "开始重新编码")

    feedback = (
        f"【自动修复】代码存在以下问题，请修复：\n"
        f"错误类型：{fix_ctx.error_type.value}\n"
        f"错误信息：{fix_ctx.error_text[:1000]}\n"
    )
    if fix_ctx.review_issues:
        from workflow.iteration_automation import build_checklist_repair_feedback

        feedback = build_checklist_repair_feedback(
            fix_ctx.review_issues,
            prior_code=fix_ctx.code,
            prior_test=fix_ctx.test_code,
            failure_reason=fix_ctx.error_text,
            module_name=fix_ctx.module_name,
        )
    elif fix_ctx.error_text:
        from workflow.review_repair_hints import augment_review_repair_hints

        feedback = augment_review_repair_hints([fix_ctx.error_text], feedback)

    try:
        from agents.module_agents import ModuleSpec as _ModuleSpec

        spec = _ModuleSpec(
            module_name=fix_ctx.module_name,
            summary=fix_ctx.spec.get("summary", ""),
            api_endpoints=fix_ctx.spec.get("api_endpoints", []),
            data_models=fix_ctx.spec.get("data_models", []),
            logic_flow=fix_ctx.spec.get("logic_flow", ""),
            error_handling=fix_ctx.spec.get("error_handling", ""),
        )
        code_result = await coder_callback(fix_ctx.module_name, spec, feedback)

        new_code = getattr(code_result, "code", "")
        new_test = getattr(code_result, "test_code", "")

        # 如果提供了审查 Agent，快速验证
        if reviewer_callback and new_code:
            from config import AUTO_FIX_QUICK_REVIEW_MAX
            for quick_review_round in range(1, AUTO_FIX_QUICK_REVIEW_MAX + 1):
                await log("INFO", f"[自愈·修改代码] 快速审查（{quick_review_round}/{AUTO_FIX_QUICK_REVIEW_MAX}）...")
                review = await reviewer_callback(
                    fix_ctx.module_name,
                    fix_ctx.spec.get("summary", ""),
                    new_code,
                    new_test,
                    retry_count=quick_review_round,
                )
                if getattr(review, "passed", False):
                    fix_ctx.record_attempt("modify_code", True, "快速审查通过")
                    return {
                        "fixed": True,
                        "code": new_code,
                        "test_code": new_test,
                        "fix_summary": "编码Agent根据错误信息重新生成代码并通过审查",
                    }
                else:
                    issues_text = "\n".join(f"  - {i}" for i in getattr(review, "issues", []))
                    await log("WARN", f"[自愈·修改代码] 快速审查不通过：\n{issues_text}")
                    if quick_review_round < AUTO_FIX_QUICK_REVIEW_MAX:
                        feedback += f"\n\n审查反馈：\n{issues_text}"
                        code_result = await coder_callback(fix_ctx.module_name, spec, feedback)
                        new_code = getattr(code_result, "code", "")
                        new_test = getattr(code_result, "test_code", "")

        fix_ctx.record_attempt("modify_code", False, "修改后审查仍未通过")
        return {"fixed": False, "reason": "修改代码后审查未通过"}
    except Exception as exc:
        fix_ctx.record_attempt("modify_code", False, str(exc))
        return {"fixed": False, "reason": f"修改代码异常: {exc}"}


async def _try_repair_agent(
    fix_ctx: FixContext,
    log,
    repair_agent_callback,
    reviewer_callback,
) -> dict[str, Any]:
    """策略 c)：调用 RepairAgent 进行反思修复。

    Returns:
        {"fixed": bool, "code": str, "test_code": str, "fix_summary": str}
    """
    if not repair_agent_callback:
        return {"fixed": False, "reason": "缺少 RepairAgent 回调"}

    await log("INFO", "[自愈·反思修复] 调用 RepairAgent 分析完整模块输出...")
    fix_ctx.record_attempt("repair_agent", False, "开始反思修复")

    module_output = {
        "module_name": fix_ctx.module_name,
        "module_type": fix_ctx.module_type,
        "spec": fix_ctx.spec,
        "code": fix_ctx.code,
        "test_code": fix_ctx.test_code,
        "error_text": fix_ctx.error_text,
        "review_issues": fix_ctx.review_issues,
    }

    try:
        repair_result = await repair_agent_callback(module_output, [])

        if not repair_result.get("can_fix", False):
            reason = repair_result.get("reason", "RepairAgent 判断无法修复")
            await log("WARN", f"[自愈·反思修复] {reason}")
            fix_ctx.record_attempt("repair_agent", False, reason)
            return {"fixed": False, "reason": reason}

        new_code = repair_result.get("code", "")
        new_test = repair_result.get("test_code", "")

        if not new_code:
            fix_ctx.record_attempt("repair_agent", False, "RepairAgent 未返回代码")
            return {"fixed": False, "reason": "RepairAgent 未返回修复代码"}

        # 快速审查
        if reviewer_callback:
            from config import AUTO_FIX_QUICK_REVIEW_MAX
            for qr in range(1, AUTO_FIX_QUICK_REVIEW_MAX + 1):
                await log("INFO", f"[自愈·反思修复] 快速审查 RepairAgent 产出（{qr}/{AUTO_FIX_QUICK_REVIEW_MAX}）...")
                review = await reviewer_callback(
                    fix_ctx.module_name,
                    fix_ctx.spec.get("summary", ""),
                    new_code,
                    new_test,
                    retry_count=qr,
                )
                if getattr(review, "passed", False):
                    fix_summary = repair_result.get("fix_summary", "RepairAgent 反思修复成功")
                    fix_ctx.record_attempt("repair_agent", True, fix_summary)
                    return {
                        "fixed": True,
                        "code": new_code,
                        "test_code": new_test,
                        "fix_summary": fix_summary,
                    }
                # 修复不通过，将问题反馈给 RepairAgent 再次修复
                issues = getattr(review, "issues", [])
                await log("WARN", f"[自愈·反思修复] 审查不通过：{'; '.join(issues[:3])}")
                if qr < AUTO_FIX_QUICK_REVIEW_MAX:
                    module_output["review_issues"] = issues
                    repair_result = await repair_agent_callback(module_output, [])
                    if not repair_result.get("can_fix"):
                        break
                    new_code = repair_result.get("code", "")
                    new_test = repair_result.get("test_code", "")

        fix_ctx.record_attempt("repair_agent", False, "修复后审查未通过")
        return {"fixed": False, "reason": "RepairAgent 修复后审查未通过"}
    except Exception as exc:
        fix_ctx.record_attempt("repair_agent", False, str(exc))
        return {"fixed": False, "reason": f"RepairAgent 异常: {exc}"}


async def _try_history_case(
    fix_ctx: FixContext,
    log,
    repair_agent_callback,
    reviewer_callback,
) -> dict[str, Any]:
    """策略 d)：检索历史修复案例并注入 RepairAgent 修复。

    Returns:
        {"fixed": bool, "code": str, "test_code": str, "fix_summary": str}
    """
    if not repair_agent_callback:
        return {"fixed": False, "reason": "缺少 RepairAgent 回调"}

    await log("INFO", "[自愈·历史案例] 检索相似历史修复案例...")
    fix_ctx.record_attempt("history_case", False, "检索历史案例")

    similar_cases = search_similar_cases(fix_ctx.error_text, fix_ctx.module_type)
    await log("INFO", f"[自愈·历史案例] 找到 {len(similar_cases)} 个相似案例")

    module_output = {
        "module_name": fix_ctx.module_name,
        "module_type": fix_ctx.module_type,
        "spec": fix_ctx.spec,
        "code": fix_ctx.code,
        "test_code": fix_ctx.test_code,
        "error_text": fix_ctx.error_text,
        "review_issues": fix_ctx.review_issues,
    }

    try:
        repair_result = await repair_agent_callback(module_output, similar_cases)

        if not repair_result.get("can_fix", False):
            reason = repair_result.get("reason", "RepairAgent（带历史案例）判断无法修复")
            fix_ctx.record_attempt("history_case", False, reason)
            return {"fixed": False, "reason": reason}

        new_code = repair_result.get("code", "")
        new_test = repair_result.get("test_code", "")

        if not new_code:
            fix_ctx.record_attempt("history_case", False, "未返回修复代码")
            return {"fixed": False, "reason": "RepairAgent 未返回修复代码"}

        # 快速审查
        if reviewer_callback:
            review = await reviewer_callback(
                fix_ctx.module_name,
                fix_ctx.spec.get("summary", ""),
                new_code,
                new_test,
                retry_count=1,
            )
            if getattr(review, "passed", False):
                fix_summary = repair_result.get("fix_summary", "参考历史案例修复成功")
                fix_ctx.record_attempt("history_case", True, fix_summary)
                return {
                    "fixed": True,
                    "code": new_code,
                    "test_code": new_test,
                    "fix_summary": fix_summary,
                }

        fix_ctx.record_attempt("history_case", False, "审查未通过")
        return {"fixed": False, "reason": "历史案例参考修复后审查未通过"}
    except Exception as exc:
        fix_ctx.record_attempt("history_case", False, str(exc))
        return {"fixed": False, "reason": f"历史案例修复异常: {exc}"}
