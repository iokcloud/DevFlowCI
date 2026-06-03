"""SSE 桥接层 — 日志推送、队列管理和流式事件。

从 workflow.executor 中独立出来。
"""

from __future__ import annotations

import asyncio
import uuid as _uuid_mod
from datetime import datetime, timezone

UTC = timezone.utc
from typing import Any, AsyncIterator

from sqlalchemy import select as _sql_select

from database.db import async_session_factory
from database.models import ModuleStatus, ModuleTask, Project, ProjectLog
from workflow.error_logger import write_error_log
from workflow.project_snapshot import fetch_project_snapshot
from workflow.stream_relay import AGENT_LABEL_MAP, push_ai_token
from logging_config import get_logger

# ── SSE 日志队列 ──────────────────────────────────────────

_log = get_logger(__name__)

# 全局字典：project_id → asyncio.Queue
_log_queues: dict[str, asyncio.Queue[dict[str, Any]]] = {}
_snapshot_last_push: dict[str, float] = {}
SNAPSHOT_DEBOUNCE_SEC = 0.8


def get_log_queue(project_id: str) -> asyncio.Queue[dict[str, Any]]:
    """获取或创建项目的 SSE 日志队列。"""
    if project_id not in _log_queues:
        _log_queues[project_id] = asyncio.Queue(maxsize=500)
    return _log_queues[project_id]


async def push_log(
    project_id: str,
    level: str,
    message: str,
    module_name: str = "",
    state_event: str = "",
) -> None:
    """推送日志到 SSE 队列 + 持久化到数据库。

    Args:
        project_id: 项目标识
        level: INFO / WARN / ERROR / SUCCESS / STATE
        message: 日志内容
        module_name: 关联模块名
        state_event: 状态变更事件（aligning/aligned/planning/plan_ready/executing/completed）
    """
    # ── structlog 结构化输出（惰性初始化）──
    try:
        log_method = getattr(_log, level.lower(), _log.info)
        log_method(message, project_id=project_id, module_name=module_name or None)
    except Exception as exc:
        _log.warning("structlog 调用失败: %s", exc)
        pass

    entry = {
        "project_id": project_id,
        "level": level,
        "message": message,
        "module_name": module_name,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if state_event:
        entry["state_event"] = state_event
        # 状态事件实时同步到 DB，避免 API 长时间显示旧状态
        try:
            asyncio.create_task(_sync_project_status(project_id, state_event))
        except Exception as exc:
            _log.warning("状态同步任务创建失败: %s", exc)
            pass
    queue = get_log_queue(project_id)
    try:
        queue.put_nowait(entry)
    except asyncio.QueueFull:
        pass  # 丢弃超额的日志
    # ★ 持久化到数据库（异步写入，失败不影响主流程）
    try:
        from database.db import async_session_factory
        from database.models import Project, ProjectLog
        from sqlalchemy import select as _sql_select
        async with async_session_factory() as _db:
            _proj = await _db.execute(_sql_select(Project).where(Project.project_id == project_id))
            _proj_row = _proj.scalar_one_or_none()
            if _proj_row:
                _plog = ProjectLog(
                    project_id_fk=_proj_row.id,
                    level=level,
                    message=message[:1000],
                    module_name=module_name if module_name else None,
                )
                _db.add(_plog)
                await _db.commit()
    except Exception as exc:
        _log.warning("日志持久化失败: %s", exc)
        pass  # 持久化失败不阻塞主流程

    # ★ ERROR / WARN 级别也写入 ErrorLog 表
        try:
            from workflow.error_logger import write_error_log
            import uuid as _uuid_mod
            _err_trace = f"log-{_uuid_mod.uuid4().hex[:12]}"
            _err_type = "review_fail" if "审查" in message else ("test_failure" if "测试" in message else "unknown")
            await write_error_log(
                project_id=project_id,
                message=message,
                trace_id=_err_trace,
                module_name=module_name if module_name else "",
                error_type=_err_type,
                stacktrace="",
            )
        except Exception as exc:
            _log.warning("ErrorLog 写入失败: %s", exc)
            pass


def remove_log_queue(project_id: str) -> None:
    """清理项目的日志队列。"""
    _log_queues.pop(project_id, None)
    _snapshot_last_push.pop(project_id, None)


async def push_project_snapshot(project_id: str, *, force: bool = False) -> None:
    """推送完整项目快照到 SSE（供 Web 实时刷新，轮询作兜底）。"""
    import time

    now = time.monotonic()
    if not force:
        last = _snapshot_last_push.get(project_id, 0.0)
        if now - last < SNAPSHOT_DEBOUNCE_SEC:
            return
    _snapshot_last_push[project_id] = now

    try:
        from workflow.project_snapshot import fetch_project_snapshot

        snapshot = await fetch_project_snapshot(project_id)
        if not snapshot:
            return
        entry: dict[str, Any] = {
            "project_id": project_id,
            "level": "SNAPSHOT",
            "message": "",
            "module_name": "",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "project_snapshot": snapshot,
        }
        queue = get_log_queue(project_id)
        queue.put_nowait(entry)
    except asyncio.QueueFull:
        pass
    except Exception as exc:
        _log.warning("项目快照推送失败: %s", exc)
        pass


async def push_module_event(
    project_id: str,
    module_name: str,
    status: str,
    *,
    failure_reason: str = "",
    description: str = "",
) -> None:
    """推送模块状态变更到 SSE，并同步写入 ModuleTask。"""
    entry: dict[str, Any] = {
        "project_id": project_id,
        "level": "MODULE",
        "message": f"[{module_name}] → {status}",
        "module_name": module_name,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "module_event": {
            "module_name": module_name,
            "status": status,
            "failure_reason": failure_reason,
            "description": description,
        },
    }
    queue = get_log_queue(project_id)
    try:
        queue.put_nowait(entry)
    except asyncio.QueueFull:
        pass

    try:
        from database.db import async_session_factory
        from database.models import ModuleStatus, ModuleTask, Project
        from sqlalchemy import select as _sel

        async with async_session_factory() as db:
            result = await db.execute(
                _sel(Project).where(Project.project_id == project_id)
            )
            project = result.scalar_one_or_none()
            if not project:
                return
            mod_result = await db.execute(
                _sel(ModuleTask).where(
                    ModuleTask.project_id_fk == project.id,
                    ModuleTask.module_name == module_name,
                )
            )
            mod = mod_result.scalar_one_or_none()
            if not mod:
                return
            try:
                mod.status = ModuleStatus(status)
            except ValueError:
                mod.status = ModuleStatus.CODING
            if failure_reason:
                mod.failure_reason = failure_reason[:2000]
            if description:
                mod.description = description[:1000]
            await db.commit()
    except Exception as exc:
        _log.warning("模块状态 DB 更新失败: %s", exc)
        pass

    await push_project_snapshot(project_id)


async def stream_logs(
    project_id: str
) -> AsyncIterator[dict[str, Any]]:
    """SSE 日志生成器。

    Args:
        project_id: 项目标识

    Yields:
        日志条目字典
    """
    queue = get_log_queue(project_id)
    while True:
        try:
            entry = await asyncio.wait_for(queue.get(), timeout=30)
            yield entry
        except asyncio.TimeoutError:
            # 发送心跳
            yield {"level": "HEARTBEAT", "message": "", "timestamp": ""}


async def _notify_ai_stream(
    project_id: str,
    agent_name: str,
    event_type: str,
    content: str = "",
) -> None:
    """推送 AI 流式事件（通知/开始/完成/输出文本）。

    Args:
        project_id: 项目标识
        agent_name: 代理名称（如 planner, alignment_agent）
        event_type: "start" / "token" / "done" / "notification"
        content: 事件内容
    """
    try:
        if event_type == "start":
            label = AGENT_LABEL_MAP.get(agent_name, agent_name)
            await push_ai_token(project_id, {
                "type": "notification",
                "agent": agent_name,
                "content": f"{label} 开始工作...",
            })
        elif event_type == "done":
            label = AGENT_LABEL_MAP.get(agent_name, agent_name)
            await push_ai_token(project_id, {
                "type": "notification",
                "agent": agent_name,
                "content": f"{label} 完成" + (f"（{content}）" if content else ""),
            })
            await push_ai_token(project_id, {
                "type": "done",
                "agent": agent_name,
                "content": "",
            })
        elif event_type == "token":
            # 将大段文本分块模拟流式推送
            chunk_size = 80
            for i in range(0, len(content), chunk_size):
                chunk = content[i : i + chunk_size]
                await push_ai_token(project_id, {
                    "type": "token",
                    "agent": agent_name,
                    "content": chunk,
                })
        elif event_type == "notification":
            await push_ai_token(project_id, {
                "type": "notification",
                "agent": agent_name,
                "content": content,
            })
    except Exception as exc:
        _log.warning("AI 流推送失败: %s", exc)
        pass  # 流式推送失败不影响主流程


async def _sync_project_status(project_id: str, status: str) -> None:
    """将工作流状态实时同步到数据库 Project 表。

    解决 plan_only / execute_from_plan 等阶段完成后，
    API 仍显示旧状态的问题。
    """
    # 测试 teardown 阶段可能没有 event loop，直接返回
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return

    try:
        from database.db import async_session_factory as _asf
        from database.models import Project as _Project, ProjectStatus as _PS
        from sqlalchemy import select as _sel

        _status_map = {
            "created": _PS.CREATED,
            "aligning": _PS.ALIGNING,
            "aligned": _PS.ALIGNED,
            "planning": _PS.PLANNING,
            "plan_ready": _PS.PLAN_READY,
            "executing": _PS.EXECUTING,
            "integrating": _PS.INTEGRATING,
            "reviewing": _PS.REVIEWING,
            "completed": _PS.COMPLETED,
            "failed": _PS.FAILED,
            "needs_review": _PS.NEEDS_REVIEW,
            "finalized": _PS.FINALIZED,
        }
        db_status = _status_map.get(status)
        if db_status is None:
            return

        async with _asf() as db:
            result = await db.execute(
                _sel(_Project).where(_Project.project_id == project_id)
            )
            project = result.scalar_one_or_none()
            if project:
                project.status = db_status
                await db.commit()
                await push_project_snapshot(project_id, force=True)
    except Exception as exc:
        _log.warning("同步项目状态失败: %s", exc)


