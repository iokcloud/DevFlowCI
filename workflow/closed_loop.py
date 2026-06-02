"""闭环修复与测试模块 — 修复前查历史、修复中记录、修复后验证循环。

提供：
1. query_fix_history(): 修复前从 DB 查询相似历史修复记录，过滤已失败策略
2. record_fix_attempt(): 将每次修复尝试写入 fix_sessions 表
3. closed_loop_repair(): 主入口 — 执行"修复→验证→重试"闭环直到成功或达上限
4. cleanup_after_fix(): 修复成功后标记所有相关 error_logs 为 resolved
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone

UTC = timezone.utc
from typing import Any

from config import (
    AUTO_FIX_MAX_TOTAL_ROUNDS,
    AUTO_FIX_QUICK_REVIEW_MAX,
)
from workflow.auto_fix import classify_error, record_fix_case, search_similar_cases

logger = logging.getLogger(__name__)

# ── 数据结构 ──────────────────────────────────────────────


@dataclass
class ClosedLoopResult:
    """闭环修复结果。"""

    success: bool
    module_name: str = ""
    fix_summary: str = ""
    total_rounds: int = 0
    strategy_used: str = ""
    code: str = ""
    test_code: str = ""
    fix_history: list[dict[str, Any]] = field(default_factory=list)
    failure_reason: str = ""


# ── 数据库查询 ──────────────────────────────────────────────


async def query_fix_history(
    project_id: str,
    module_name: str,
    error_type: str = "",
) -> dict[str, Any]:
    """修复前查询：从 error_logs 和 fix_sessions 中检索历史修复记录。

    Returns:
        {
            "past_errors": [...],       # 相同模块的过往错误
            "past_fixes": [...],        # 该项目的过往修复记录
            "failed_strategies": [...], # 已尝试过的失败策略
            "similar_module_fixes": [...], # 其他模块的相似错误修复
            "summary": "人类可读的摘要"
        }
    """
    result: dict[str, Any] = {
        "past_errors": [],
        "past_fixes": [],
        "failed_strategies": [],
        "similar_module_fixes": [],
        "summary": "",
    }

    try:
        from sqlalchemy import select

        from database.db import async_session_factory
        from database.models import ErrorLog, ErrorStatus, FixSession

        async with async_session_factory() as db:
            # 1. 查询 open 状态的过往错误
            err_stmt = (
                select(ErrorLog)
                .where(
                    ErrorLog.project_id == project_id,
                    ErrorLog.status == ErrorStatus.OPEN,
                )
                .order_by(ErrorLog.created_at.desc())
                .limit(20)
            )
            err_result = await db.execute(err_stmt)
            for err in err_result.scalars().all():
                result["past_errors"].append({
                    "trace_id": err.trace_id,
                    "module_name": err.module_name,
                    "error_type": err.error_type,
                    "message": err.message[:300],
                    "created_at": err.created_at.isoformat() if err.created_at else "",
                })

            # 2. 查询该项目的修复历史
            fix_stmt = (
                select(FixSession)
                .where(
                    FixSession.project_id == project_id,
                )
                .order_by(FixSession.created_at.desc())
                .limit(30)
            )
            fix_result = await db.execute(fix_stmt)
            for fix in fix_result.scalars().all():
                entry = {
                    "module_name": fix.module_name,
                    "error_type": fix.error_type,
                    "error_message": (fix.error_message or "")[:200],
                    "strategy_used": fix.strategy_used,
                    "success": fix.success,
                    "fix_summary": fix.fix_summary or "",
                    "round_number": fix.round_number,
                    "loop_count": fix.loop_count,
                    "created_at": fix.created_at.isoformat() if fix.created_at else "",
                }
                result["past_fixes"].append(entry)
                if not fix.success:
                    result["failed_strategies"].append(fix.strategy_used)

            # 3. 查询其他项目中相似 error_type 的成功修复
            if error_type:
                similar_stmt = (
                    select(FixSession)
                    .where(
                        FixSession.success == 1,
                        FixSession.error_type == error_type,
                        FixSession.project_id != project_id,
                    )
                    .order_by(FixSession.created_at.desc())
                    .limit(5)
                )
                similar_result = await db.execute(similar_stmt)
                for fix in similar_result.scalars().all():
                    result["similar_module_fixes"].append({
                        "module_name": fix.module_name,
                        "strategy_used": fix.strategy_used,
                        "fix_summary": fix.fix_summary or "",
                    })

    except Exception as exc:
        result["summary"] = f"数据库查询异常: {exc}"

    # 构建摘要
    parts: list[str] = []
    if result["past_errors"]:
        parts.append(f"该模块有 {len(result['past_errors'])} 条待处理错误")
    if result["failed_strategies"]:
        unique_failed = list(set(result["failed_strategies"]))
        parts.append(f"已失败的策略: {', '.join(unique_failed)}")
    if result["similar_module_fixes"]:
        parts.append(f"找到 {len(result['similar_module_fixes'])} 条相似成功修复参考")
    result["summary"] = "；".join(parts) if parts else "无历史修复记录"

    return result


async def record_fix_attempt(
    project_id: str,
    module_name: str,
    error_type: str,
    error_message: str,
    strategy_used: str,
    round_number: int,
    success: bool,
    fix_summary: str = "",
    code_before: str = "",
    code_after: str = "",
    test_result: str = "",
    loop_count: int = 0,
) -> int | None:
    """将单次修复尝试写入 fix_sessions 表。

    Returns:
        数据库记录 ID，写入失败返回 None
    """
    try:
        from sqlalchemy import select as _sel

        from database.db import async_session_factory
        from database.models import FixSession, Project

        async with async_session_factory() as db:
            proj = await db.execute(
                _sel(Project).where(Project.project_id == project_id)
            )
            proj_row = proj.scalar_one_or_none()
            if not proj_row:
                return None

            session = FixSession(
                project_id_fk=proj_row.id,
                project_id=project_id,
                module_name=module_name,
                error_type=error_type,
                error_message=error_message[:2000] if error_message else None,
                strategy_used=strategy_used,
                round_number=round_number,
                success=1 if success else 0,
                fix_summary=fix_summary[:1000] if fix_summary else None,
                code_before=code_before[:4000] if code_before else None,
                code_after=code_after[:4000] if code_after else None,
                test_result=test_result[:2000] if test_result else None,
                loop_count=loop_count,
                resolved_at=datetime.now(UTC) if success else None,
            )
            db.add(session)
            await db.commit()
            return session.id
    except Exception as exc:
        logger.warning("写入修复记录失败: %s", exc)
        return None


async def cleanup_after_fix(
    project_id: str,
    module_name: str,
) -> dict[str, Any]:
    """修复成功后：将所有相关 open 错误标记为 resolved，返回清理统计。

    Returns:
        {"resolved_count": N, "cleaned_files": M}
    """
    stats = {"resolved_count": 0, "cleaned_files": 0}

    try:
        from sqlalchemy import select

        from database.db import async_session_factory
        from database.models import ErrorLog, ErrorStatus

        async with async_session_factory() as db:
            # 查找所有 open 状态的错误
            err_stmt = select(ErrorLog).where(
                ErrorLog.project_id == project_id,
                ErrorLog.module_name == module_name,
                ErrorLog.status == ErrorStatus.OPEN,
            )
            err_result = await db.execute(err_stmt)
            errors = err_result.scalars().all()

            for err in errors:
                err.status = ErrorStatus.RESOLVED
                stats["resolved_count"] += 1

            await db.commit()

    except Exception as exc:
        logger.warning("数据库标记错误日志为 resolved 失败: %s", exc)
        pass

    # 同时更新 auto_fix_cases.json（如果存在）
    try:

        # 清理过期的修复案例文件（超过30天的标记为 archived）
        from config import AUTO_FIX_CASES_FILE
        if AUTO_FIX_CASES_FILE.exists():
            data = json.loads(AUTO_FIX_CASES_FILE.read_text(encoding="utf-8"))
            cases = data.get("cases", [])
            cutoff = datetime.now(UTC).timestamp() - 30 * 86400
            active_cases = []
            archived_count = 0
            for case in cases:
                try:
                    case_time = datetime.fromisoformat(case.get("created_at", "")).timestamp()
                    if case_time >= cutoff:
                        active_cases.append(case)
                    else:
                        archived_count += 1
                except Exception:
                    # 日期解析失败，保留该案例
                    active_cases.append(case)

            if archived_count > 0:
                data["cases"] = active_cases
                AUTO_FIX_CASES_FILE.write_text(
                    json.dumps(data, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                stats["cleaned_files"] = archived_count
    except Exception as exc:
        logger.warning("清理过期修复案例文件失败: %s", exc)
        pass


# ── 闭环修复主入口 ──────────────────────────────────────────


async def closed_loop_repair(
    *,
    project_id: str,
    module_name: str,
    module_type: str,
    spec_summary: str,
    spec_detail: dict[str, Any],
    current_code: str,
    current_test_code: str,
    review_issues: list[str],
    failure_reason: str,
    # 回调函数（由 executor 注入）
    do_code: Callable[..., Awaitable[Any]],
    do_review: Callable[..., Awaitable[Any]],
    do_repair: Callable[..., Awaitable[Any]],
    do_test: Callable[..., Awaitable[Any]],
    push_log: Callable[..., Awaitable[Any]],
    max_total_rounds: int = AUTO_FIX_MAX_TOTAL_ROUNDS,
    quick_review_max: int = AUTO_FIX_QUICK_REVIEW_MAX,
) -> ClosedLoopResult:
    """闭环修复主循环：查询历史 → 尝试修复 → 验证 → 重试。

    流程：
    1. 查询 DB 中的历史修复记录，过滤已失败的策略
    2. 按优先级尝试各修复策略
    3. 每次修复后执行测试验证
    4. 失败则记录并进入下一轮（带回更丰富的上下文）
    5. 成功则清理所有相关错误日志
    6. 达到最大轮次仍未成功则返回失败

    Args:
        project_id: 项目标识
        module_name: 模块名
        module_type: 模块类型
        spec_summary: 规格摘要
        spec_detail: 完整规格详情
        current_code: 当前代码
        current_test_code: 当前测试代码
        review_issues: 审查指出的问题列表
        failure_reason: 失败原因描述
        do_code: 编码回调 async fn(module_name, spec, feedback) -> ModuleCode
        do_review: 审查回调 async fn(module_name, summary, code, test_code, retry_count=0) -> ReviewResult
        do_repair: 修复回调 async fn(module_output, history_cases) -> RepairResult
        do_test: 测试回调 async fn(module_name, test_code, module_code="") -> TestResult | None
        push_log: 日志回调 async fn(pid, level, msg, module_name)
        max_total_rounds: 最大总轮次
        quick_review_max: 快速审查最大次数

    Returns:
        ClosedLoopResult
    """
    from workflow.iteration_automation import (
        build_checklist_repair_feedback,
        is_preserve_worthy_code,
    )

    error_type = classify_error(failure_reason)
    fix_history: list[dict[str, Any]] = []
    code = current_code
    test_code = current_test_code
    feedback = ""
    if review_issues and is_preserve_worthy_code(current_code, current_test_code):
        feedback = build_checklist_repair_feedback(
            review_issues,
            prior_code=current_code,
            prior_test=current_test_code,
            failure_reason=failure_reason,
            module_name=module_name,
        )

    await push_log(
        project_id, "INFO",
        f"[闭环修复][{module_name}] 开始闭环修复，错误类型: {error_type.value}",
        module_name=module_name,
    )

    # ── 步骤1：查询历史修复记录 ──
    await push_log(
        project_id, "INFO",
        f"[闭环修复][{module_name}] 查询历史修复记录...",
        module_name=module_name,
    )
    history = await query_fix_history(project_id, module_name, error_type.value)

    await push_log(
        project_id, "INFO",
        f"[闭环修复][{module_name}] {history['summary']}",
        module_name=module_name,
    )

    # 从历史中排除已失败的策略
    failed_strategies_set = set(history.get("failed_strategies", []))

    # 确定尝试策略优先级（跳过已知失败的）
    strategy_order = (
        ["modify_code", "repair_agent", "history_case", "direct_retry"]
        if feedback
        else ["direct_retry", "modify_code", "repair_agent", "history_case"]
    )
    strategies: list[str] = [
        s for s in strategy_order if s not in failed_strategies_set
    ]

    if not strategies:
        await push_log(
            project_id, "WARN",
            f"[闭环修复][{module_name}] 所有策略均已尝试失败，使用全部策略重试",
            module_name=module_name,
        )
        strategies = ["direct_retry", "modify_code", "repair_agent", "history_case"]

    # ── 步骤2：闭环修复循环 ──
    total_rounds = 0
    loop_count = 0

    for strategy in strategies:
        if total_rounds >= max_total_rounds:
            break

        loop_count += 1
        total_rounds += 1

        await push_log(
            project_id, "INFO",
            f"[闭环修复][{module_name}] 第{loop_count}轮 · 策略: {strategy} ({total_rounds}/{max_total_rounds})",
            module_name=module_name,
        )

        fix_entry = {
            "round": total_rounds,
            "loop": loop_count,
            "strategy": strategy,
            "success": False,
            "detail": "",
            "timestamp": datetime.now(UTC).isoformat(),
        }

        try:
            if strategy == "direct_retry":
                delay = min(2.0 * (2.0 ** (loop_count - 1)), 60.0)
                await push_log(
                    project_id, "INFO",
                    f"[闭环修复·退避][{module_name}] 等待 {delay:.1f}s 后退避重试...",
                    module_name=module_name,
                )
                await asyncio.sleep(delay)

                code_obj = await do_code(module_name, spec_detail, feedback)
                review_result = await do_review(
                    module_name, spec_summary,
                    code_obj.code if hasattr(code_obj, 'code') else code_obj.get('code', ''),
                    code_obj.test_code if hasattr(code_obj, 'test_code') else code_obj.get('test_code', ''),
                    retry_count=total_rounds,
                )

                code = code_obj.code if hasattr(code_obj, 'code') else code_obj.get('code', '')
                test_code = code_obj.test_code if hasattr(code_obj, 'test_code') else code_obj.get('test_code', '')

                if review_result.passed if hasattr(review_result, 'passed') else review_result.get('passed', False):
                    # 执行实际测试验证
                    test_ok = True
                    try:
                        test_result = await do_test(module_name, test_code, code)
                        test_ok = test_result.passed if test_result and hasattr(test_result, 'passed') else True
                    except Exception as te:
                        await push_log(project_id, "WARN", f"[闭环修复][{module_name}] 测试验证异常: {te}", module_name=module_name)
                        test_ok = False

                    if test_ok:
                        fix_entry["success"] = True
                        fix_entry["detail"] = "退避重试成功，审查通过+测试通过"
                        fix_history.append(fix_entry)
                        await record_fix_attempt(
                            project_id, module_name, error_type.value, failure_reason,
                            strategy, total_rounds, True,
                            fix_summary="退避重试成功", code_before=current_code,
                            code_after=code, test_result="passed", loop_count=loop_count,
                        )
                        await push_log(project_id, "SUCCESS", f"[闭环修复][{module_name}] 退避重试+测试验证通过！", module_name=module_name)
                        await _resolve_and_cleanup(project_id, module_name, push_log)
                        return ClosedLoopResult(
                            success=True, module_name=module_name,
                            fix_summary="退避重试成功", total_rounds=total_rounds,
                            strategy_used=strategy, code=code, test_code=test_code,
                            fix_history=fix_history,
                        )
                    else:
                        fix_entry["detail"] = "审查通过但测试验证失败"
                        feedback = f"测试验证失败，请修复：\n{review_result.summary if hasattr(review_result, 'summary') else ''}"

                feedback = "审查反馈：\n" + "\n".join(f"  - {i}" for i in (review_result.issues if hasattr(review_result, 'issues') else review_result.get('issues', [])))

            elif strategy == "modify_code":
                await push_log(project_id, "INFO", f"[闭环修复·修改代码][{module_name}] 注入审查反馈...", module_name=module_name)
                code_obj = await do_code(module_name, spec_detail, feedback)
                code = code_obj.code if hasattr(code_obj, 'code') else code_obj.get('code', '')
                test_code = code_obj.test_code if hasattr(code_obj, 'test_code') else code_obj.get('test_code', '')

                for qr in range(quick_review_max):
                    review_result = await do_review(
                        module_name, spec_summary, code, test_code, retry_count=total_rounds,
                    )
                    review_passed = review_result.passed if hasattr(review_result, 'passed') else review_result.get('passed', False)

                    if review_passed:
                        test_ok = True
                        try:
                            test_result = await do_test(module_name, test_code, code)
                            test_ok = test_result.passed if test_result and hasattr(test_result, 'passed') else True
                        except Exception:
                            test_ok = False

                        if test_ok:
                            fix_entry["success"] = True
                            fix_entry["detail"] = f"修改代码成功（快速审查第{qr+1}次）· 测试通过"
                            fix_history.append(fix_entry)
                            await record_fix_attempt(
                                project_id, module_name, error_type.value, failure_reason,
                                strategy, total_rounds, True,
                                fix_summary=f"修改代码+{qr+1}次审查+测试通过",
                                code_before=current_code, code_after=code,
                                test_result="passed", loop_count=loop_count,
                            )
                            await push_log(project_id, "SUCCESS", f"[闭环修复][{module_name}] 修改代码+测试验证通过！", module_name=module_name)
                            await _resolve_and_cleanup(project_id, module_name, push_log)
                            return ClosedLoopResult(
                                success=True, module_name=module_name,
                                fix_summary="修改代码+审查通过", total_rounds=total_rounds,
                                strategy_used=strategy, code=code, test_code=test_code,
                                fix_history=fix_history,
                            )

                    if qr < quick_review_max - 1:
                        issues = review_result.issues if hasattr(review_result, 'issues') else review_result.get('issues', [])
                        feedback = "审查反馈：\n" + "\n".join(f"  - {i}" for i in issues)
                        code_obj = await do_code(module_name, spec_detail, feedback)
                        code = code_obj.code if hasattr(code_obj, 'code') else code_obj.get('code', '')
                        test_code = code_obj.test_code if hasattr(code_obj, 'test_code') else code_obj.get('test_code', '')

                feedback = "审查反馈：\n" + "\n".join(f"  - {i}" for i in (review_result.issues if hasattr(review_result, 'issues') else []))

            elif strategy == "repair_agent":
                await push_log(project_id, "INFO", f"[闭环修复·反思][{module_name}] 调用 RepairAgent...", module_name=module_name)

                # 获取相似历史案例
                similar_cases = search_similar_cases(failure_reason, module_type)

                module_output = {
                    "module_name": module_name,
                    "module_type": module_type,
                    "spec": spec_detail,
                    "code": code if code else current_code,
                    "test_code": test_code if test_code else current_test_code,
                    "error_text": failure_reason,
                    "review_issues": review_issues,
                }
                repair_result = await do_repair(module_output, similar_cases)

                if not (repair_result.can_fix if hasattr(repair_result, 'can_fix') else repair_result.get('can_fix', False)):
                    fix_entry["detail"] = f"RepairAgent 判断无法修复: {repair_result.reason if hasattr(repair_result, 'reason') else repair_result.get('reason', '')}"
                    fix_history.append(fix_entry)
                    await record_fix_attempt(
                        project_id, module_name, error_type.value, failure_reason,
                        strategy, total_rounds, False,
                        fix_summary=f"无法修复: {fix_entry['detail']}",
                        code_before=current_code, code_after="", loop_count=loop_count,
                    )
                    continue

                code = repair_result.code if hasattr(repair_result, 'code') else repair_result.get('code', '')
                test_code = repair_result.test_code if hasattr(repair_result, 'test_code') else repair_result.get('test_code', '')

                for qr in range(quick_review_max):
                    review_result = await do_review(
                        module_name, spec_summary, code, test_code, retry_count=total_rounds,
                    )
                    review_passed = review_result.passed if hasattr(review_result, 'passed') else review_result.get('passed', False)

                    if review_passed:
                        test_ok = True
                        try:
                            test_result = await do_test(module_name, test_code, code)
                            test_ok = test_result.passed if test_result and hasattr(test_result, 'passed') else True
                        except Exception:
                            test_ok = False  # swallow: test execution is best-effort

                        if test_ok:
                            fix_summary = repair_result.fix_summary if hasattr(repair_result, 'fix_summary') else repair_result.get('fix_summary', '')
                            fix_entry["success"] = True
                            fix_entry["detail"] = f"RepairAgent 修复成功: {fix_summary}"
                            fix_history.append(fix_entry)
                            await record_fix_attempt(
                                project_id, module_name, error_type.value, failure_reason,
                                strategy, total_rounds, True,
                                fix_summary=fix_summary,
                                code_before=current_code, code_after=code,
                                test_result="passed", loop_count=loop_count,
                            )
                            record_fix_case(
                                error_text=failure_reason,
                                module_name=module_name,
                                module_type=module_type,
                                fix_summary=fix_summary,
                                strategy_used=strategy,
                            )
                            await push_log(project_id, "SUCCESS", f"[闭环修复][{module_name}] RepairAgent+测试验证通过！", module_name=module_name)
                            await _resolve_and_cleanup(project_id, module_name, push_log)
                            return ClosedLoopResult(
                                success=True, module_name=module_name,
                                fix_summary=fix_summary, total_rounds=total_rounds,
                                strategy_used=strategy, code=code, test_code=test_code,
                                fix_history=fix_history,
                            )

                    if qr < quick_review_max - 1:
                        issues = review_result.issues if hasattr(review_result, 'issues') else review_result.get('issues', [])
                        module_output["review_issues"] = issues
                        repair_result = await do_repair(module_output, similar_cases)
                        if not (repair_result.can_fix if hasattr(repair_result, 'can_fix') else repair_result.get('can_fix', False)):
                            break
                        code = repair_result.code if hasattr(repair_result, 'code') else repair_result.get('code', '')
                        test_code = repair_result.test_code if hasattr(repair_result, 'test_code') else repair_result.get('test_code', '')

            elif strategy == "history_case":
                await push_log(project_id, "INFO", f"[闭环修复·历史案例][{module_name}] 检索相似案例...", module_name=module_name)
                similar = search_similar_cases(failure_reason, module_type)
                await push_log(project_id, "INFO", f"[闭环修复·历史案例][{module_name}] 找到 {len(similar)} 个相似案例", module_name=module_name)

                if not similar and history.get("similar_module_fixes"):
                    # 把DB查询结果也注入
                    similar = history["similar_module_fixes"]
                    await push_log(project_id, "INFO", f"[闭环修复·历史案例][{module_name}] 使用 DB 中的 {len(similar)} 条成功修复", module_name=module_name)

                module_output = {
                    "module_name": module_name,
                    "module_type": module_type,
                    "spec": spec_detail,
                    "code": code if code else current_code,
                    "test_code": test_code if test_code else current_test_code,
                    "error_text": failure_reason,
                    "review_issues": review_issues,
                }
                repair_result = await do_repair(module_output, similar)

                if repair_result.can_fix if hasattr(repair_result, 'can_fix') else repair_result.get('can_fix', False):
                    code = repair_result.code if hasattr(repair_result, 'code') else repair_result.get('code', '')
                    test_code = repair_result.test_code if hasattr(repair_result, 'test_code') else repair_result.get('test_code', '')

                    review_result = await do_review(
                        module_name, spec_summary, code, test_code, retry_count=total_rounds,
                    )
                    review_passed = review_result.passed if hasattr(review_result, 'passed') else review_result.get('passed', False)

                    if review_passed:
                        test_ok = True
                        try:
                            test_result = await do_test(module_name, test_code, code)
                            test_ok = test_result.passed if test_result and hasattr(test_result, 'passed') else True
                        except Exception:
                            test_ok = False

                        if test_ok:
                            fix_summary = repair_result.fix_summary if hasattr(repair_result, 'fix_summary') else repair_result.get('fix_summary', '')
                            fix_entry["success"] = True
                            fix_entry["detail"] = f"历史案例参考修复成功: {fix_summary}"
                            fix_history.append(fix_entry)
                            await record_fix_attempt(
                                project_id, module_name, error_type.value, failure_reason,
                                strategy, total_rounds, True,
                                fix_summary=fix_summary,
                                code_before=current_code, code_after=code,
                                test_result="passed", loop_count=loop_count,
                            )
                            record_fix_case(
                                error_text=failure_reason,
                                module_name=module_name,
                                module_type=module_type,
                                fix_summary=fix_summary,
                                strategy_used=strategy,
                            )
                            await push_log(project_id, "SUCCESS", f"[闭环修复][{module_name}] 历史案例+测试验证通过！", module_name=module_name)
                            await _resolve_and_cleanup(project_id, module_name, push_log)
                            return ClosedLoopResult(
                                success=True, module_name=module_name,
                                fix_summary=fix_summary, total_rounds=total_rounds,
                                strategy_used=strategy, code=code, test_code=test_code,
                                fix_history=fix_history,
                            )

            # 本轮策略未成功，记录失败
            fix_history.append(fix_entry)
            await record_fix_attempt(
                project_id, module_name, error_type.value, failure_reason,
                strategy, total_rounds, False,
                fix_summary=fix_entry.get("detail", ""),
                code_before=current_code, code_after=code if code else "",
                loop_count=loop_count,
            )

            # ★ 每轮结束后重新查询DB，刷新上下文（可能其他模块已修复类似问题）
            if loop_count > 1 and loop_count % 2 == 0:
                await push_log(project_id, "INFO", f"[闭环修复][{module_name}] 刷新历史修复记录...", module_name=module_name)
                history = await query_fix_history(project_id, module_name, error_type.value)
                if history.get("similar_module_fixes"):
                    await push_log(
                        project_id, "INFO",
                        f"[闭环修复][{module_name}] 新找到 {len(history['similar_module_fixes'])} 条成功案例",
                        module_name=module_name,
                    )

        except Exception as exc:
            fix_entry["detail"] = f"策略 '{strategy}' 异常: {exc}"
            fix_history.append(fix_entry)
            await push_log(project_id, "ERROR", f"[闭环修复][{module_name}] 策略异常: {exc}", module_name=module_name)
            await record_fix_attempt(
                project_id, module_name, error_type.value, failure_reason,
                strategy, total_rounds, False,
                fix_summary=f"异常: {exc}",
                code_before=current_code, loop_count=loop_count,
            )

    # ── 步骤3：所有策略尝试完毕，标记为最终失败 ──
    final_reason = failure_reason or "所有闭环修复策略已尝试完毕"
    await push_log(
        project_id, "WARN",
        f"[闭环修复][{module_name}] {loop_count} 轮修复均未通过，最终阻塞",
        module_name=module_name,
    )

    return ClosedLoopResult(
        success=False,
        module_name=module_name,
        fix_summary=f"共{loop_count}轮修复失败",
        total_rounds=total_rounds,
        strategy_used="all_exhausted",
        code=code if code else current_code,
        test_code=test_code if test_code else current_test_code,
        fix_history=fix_history,
        failure_reason=final_reason,
    )


# ── 内部辅助 ──────────────────────────────────────────────


async def _resolve_and_cleanup(
    project_id: str,
    module_name: str,
    push_log: Callable[..., Awaitable[Any]],
) -> None:
    """修复成功后的清理：标记 error_logs 为 resolved。"""
    try:
        stats = await cleanup_after_fix(project_id, module_name)
        if stats["resolved_count"] > 0:
            await push_log(
                project_id, "SUCCESS",
                f"[闭环修复][{module_name}] 已解决 {stats['resolved_count']} 条错误日志",
                module_name=module_name,
            )
        if stats.get("cleaned_files", 0) > 0:
            await push_log(
                project_id, "INFO",
                f"[闭环修复][{module_name}] 已归档 {stats['cleaned_files']} 条过期案例",
                module_name=module_name,
            )
    except Exception as exc:
        await push_log(
            project_id, "WARN",
            f"[闭环修复][{module_name}] 清理异常: {exc}",
            module_name=module_name,
        )
