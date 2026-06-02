"""统一错误日志模块 — 写数据库 + 文件双通道。

提供：
1. write_error_log(): 写入 ErrorLog 表 + 同步日志文件
2. resolve_error(): 标记错误为已修复
3. get_error_stats(): 查询错误统计（按模块/类型聚合）
4. clean_resolved_logs(): 清理旧日志
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

UTC = timezone.utc
from pathlib import Path
from typing import Any

from config import PROJECT_ROOT

logger = logging.getLogger(__name__)

# ── 日志文件路径 ──────────────────────────────────────────────
ERROR_LOG_FILE: Path = PROJECT_ROOT / "server.log"

# ── Python 标准日志器 ────────────────────────────────────────
_logger = logging.getLogger("devflow.error")


async def write_error_log(
    project_id: str,
    message: str,
    *,
    trace_id: str | None = None,
    module_name: str = "",
    error_type: str = "unknown",
    stacktrace: str = "",
) -> int | None:
    """写入错误日志（数据库 + 文件双写）。

    Args:
        project_id: 项目标识
        message: 错误消息
        trace_id: 跨模块追踪标识（为空时自动生成）
        module_name: 关联模块名
        error_type: 错误类型（api_timeout / syntax_error / review_fail 等）
        stacktrace: 堆栈信息

    Returns:
        数据库记录的 ID，写入失败时返回 None
    """
    if trace_id is None:
        trace_id = f"err-{uuid.uuid4().hex[:12]}"

    # ── 1. 写数据库 ──
    db_id: int | None = None
    try:
        from database.db import async_session_factory
        from database.models import ErrorLog, ErrorStatus
        async with async_session_factory() as db:
            err = ErrorLog(
                trace_id=trace_id,
                project_id=project_id,
                module_name=module_name if module_name else None,
                error_type=error_type,
                message=message[:2000],
                stacktrace=stacktrace[:5000] if stacktrace else None,
                status=ErrorStatus.OPEN,
            )
            db.add(err)
            await db.commit()
            db_id = err.id
    except Exception as exc:
        _logger.warning(f"错误日志数据库写入失败: {exc}")

    # ── 2. 写文件日志 ──
    try:
        timestamp = datetime.now(UTC).isoformat()
        log_entry = (
            f"[{timestamp}] [ERROR] [{project_id}]"
            + (f" [{module_name}]" if module_name else "")
            + f" [{error_type}] {message}"
            + (f"\nSTACKTRACE: {stacktrace[:2000]}" if stacktrace else "")
        )
        with open(ERROR_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(log_entry + "\n")
    except Exception as exc:
        logger.warning("错误日志写入文件失败: %s", exc)
        pass

    return db_id


async def resolve_error(
    trace_id: str,
    *,
    resolved_by: str = "auto_fix",
    fix_detail: str = "",
) -> bool:
    """将指定 trace_id 的所有 open 错误标记为 resolved。

    Args:
        trace_id: 追踪标识
        resolved_by: 修复方式（auto_fix / manual / cleanup）
        fix_detail: 修复详情

    Returns:
        是否成功标记
    """
    try:
        from sqlalchemy import update

        from database.db import async_session_factory
        from database.models import ErrorLog, ErrorStatus

        async with async_session_factory() as db:
            stmt = (
                update(ErrorLog)
                .where(
                    ErrorLog.trace_id == trace_id,
                    ErrorLog.status == ErrorStatus.OPEN,
                )
                .values(status=ErrorStatus.RESOLVED)
            )
            result = await db.execute(stmt)
            await db.commit()

            count = result.rowcount  # type: ignore[union-attr]
            if count > 0:
                _logger.info(
                    f"已将 trace_id={trace_id} 的 {count} 条错误标记为 resolved"
                )
                return True
            return False
    except Exception as exc:
        _logger.warning(f"标记错误为 resolved 失败: {exc}")
        return False


async def get_error_stats(
    project_id: str | None = None,
) -> dict[str, Any]:
    """查询错误统计。

    Args:
        project_id: 可选，按项目过滤

    Returns:
        {
            "total": int,
            "open": int,
            "resolved": int,
            "by_module": [{"module_name": "...", "count": int, "open": int}, ...],
            "by_type": [{"error_type": "...", "count": int}, ...],
            "recent": [{"id": int, "message": str, "module_name": str, "created_at": str}, ...],
        }
    """
    try:
        from sqlalchemy import case, func, select

        from database.db import async_session_factory
        from database.models import ErrorLog, ErrorStatus

        async with async_session_factory() as db:
            # 总数统计
            conditions = []
            if project_id:
                conditions.append(ErrorLog.project_id == project_id)

            base = select(ErrorLog)
            if conditions:
                base = base.where(*conditions)

            total_result = await db.execute(
                select(func.count()).select_from(base.alias())
            )
            total = total_result.scalar() or 0

            open_result = await db.execute(
                select(func.count()).select_from(
                    base.where(ErrorLog.status == ErrorStatus.OPEN).alias()
                )
            )
            open_count = open_result.scalar() or 0

            resolved_count = total - open_count

            # 按模块聚合
            module_agg = await db.execute(
                select(
                    ErrorLog.module_name,
                    func.count().label("cnt"),
                    func.sum(
                        case(
                            (ErrorLog.status == ErrorStatus.OPEN, 1),
                            else_=0,
                        )
                    ).label("open_cnt"),
                )
                .where(*conditions)
                .group_by(ErrorLog.module_name)
                .order_by(func.count().desc())
                .limit(20)
            )
            by_module = [
                {
                    "module_name": row.module_name or "未知",
                    "count": row.cnt,
                    "open": row.open_cnt,
                }
                for row in module_agg
            ]

            # 按错误类型聚合
            type_agg = await db.execute(
                select(
                    ErrorLog.error_type,
                    func.count().label("cnt"),
                )
                .where(*conditions)
                .group_by(ErrorLog.error_type)
                .order_by(func.count().desc())
                .limit(15)
            )
            by_type = [
                {"error_type": row.error_type or "unknown", "count": row.cnt}
                for row in type_agg
            ]

            # 最近错误
            recent_result = await db.execute(
                select(ErrorLog)
                .where(*conditions)
                .order_by(ErrorLog.created_at.desc())
                .limit(10)
            )
            recent = [
                {
                    "id": e.id,
                    "message": e.message[:200],
                    "module_name": e.module_name or "",
                    "error_type": e.error_type or "",
                    "status": e.status.value,
                    "created_at": e.created_at.isoformat(),
                }
                for e in recent_result.scalars().all()
            ]

            return {
                "total": total,
                "open": open_count,
                "resolved": resolved_count,
                "by_module": by_module,
                "by_type": by_type,
                "recent": recent,
            }
    except Exception as exc:
        _logger.warning(f"查询错误统计失败: {exc}")
        return {
            "total": 0,
            "open": 0,
            "resolved": 0,
            "by_module": [],
            "by_type": [],
            "recent": [],
            "error": str(exc),
        }


async def get_open_errors(
    project_id: str | None = None,
    module_name: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """查询 status='open' 的错误日志。

    Args:
        project_id: 可选，按项目过滤
        module_name: 可选，按模块过滤
        limit: 返回数量上限

    Returns:
        错误日志字典列表
    """
    try:
        from sqlalchemy import select

        from database.db import async_session_factory
        from database.models import ErrorLog, ErrorStatus

        async with async_session_factory() as db:
            conditions = [ErrorLog.status == ErrorStatus.OPEN]
            if project_id:
                conditions.append(ErrorLog.project_id == project_id)
            if module_name:
                conditions.append(ErrorLog.module_name == module_name)

            result = await db.execute(
                select(ErrorLog)
                .where(*conditions)
                .order_by(ErrorLog.created_at.desc())
                .limit(limit)
            )
            return [
                {
                    "id": e.id,
                    "trace_id": e.trace_id,
                    "project_id": e.project_id,
                    "module_name": e.module_name,
                    "error_type": e.error_type,
                    "message": e.message,
                    "stacktrace": e.stacktrace,
                    "created_at": e.created_at.isoformat(),
                }
                for e in result.scalars().all()
            ]
    except Exception as exc:
        _logger.warning(f"查询 open 错误失败: {exc}")
        return []


async def clean_resolved_logs(
    days: int = 7,
    dry_run: bool = False,
) -> dict[str, Any]:
    """清理指定天数前且 status='resolved' 的错误日志。

    Args:
        days: 保留天数，删除 N 天前的已解决错误
        dry_run: True 时仅统计不删除

    Returns:
        {"deleted_count": int, "dry_run": bool}
    """
    cutoff = datetime.now(UTC) - timedelta(days=days)
    try:
        from sqlalchemy import delete, func, select

        from database.db import async_session_factory
        from database.models import ErrorLog, ErrorStatus

        async with async_session_factory() as db:
            # 先统计
            count_result = await db.execute(
                select(func.count()).select_from(
                    select(ErrorLog)
                    .where(
                        ErrorLog.status == ErrorStatus.RESOLVED,
                        ErrorLog.created_at < cutoff,
                    )
                    .alias()
                )
            )
            count = count_result.scalar() or 0

            if not dry_run and count > 0:
                stmt = delete(ErrorLog).where(
                    ErrorLog.status == ErrorStatus.RESOLVED,
                    ErrorLog.created_at < cutoff,
                )
                await db.execute(stmt)
                await db.commit()

            return {"deleted_count": count, "dry_run": dry_run}
    except Exception as exc:
        _logger.warning(f"清理错误日志失败: {exc}")
        return {"deleted_count": 0, "dry_run": dry_run, "error": str(exc)}
