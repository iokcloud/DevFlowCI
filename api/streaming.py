"""SSE 流式端点。"""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from database.db import async_session_factory
from database.models import Project
from workflow.sse_bridge import stream_logs, remove_log_queue
from workflow.stream_relay import stream_ai_tokens

router = APIRouter(prefix="/api/projects", tags=["streaming"])

@router.get("/{project_id}/logs")
async def get_logs_sse(project_id: str) -> StreamingResponse:
    """实时日志流（SSE）。

    GET /api/projects/{project_id}/logs
    """
    from sqlalchemy import select

    from database.db import async_session_factory

    # 检查项目是否存在
    async with async_session_factory() as db:
        result = await db.execute(
            select(Project).where(Project.project_id == project_id)
        )
        if not result.scalar_one_or_none():
            raise HTTPException(404, "项目不存在")

    async def event_generator():
        async for entry in stream_logs(project_id):
            yield f"data: {json.dumps(entry, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/{project_id}/stream-ai")
async def get_ai_stream(project_id: str) -> StreamingResponse:
    """实时 AI 输出流（SSE）—— 接收 AI token 和系统通知。

    GET /api/projects/{project_id}/stream-ai

    推送事件类型：
    - type: "token"       — AI 实时输出的 token 片段
    - type: "notification" — 系统通知（Agent 开始/完成）
    - type: "done"        — 当前 AI 任务完成
    - type: "error"       — AI 调用异常
    - type: "heartbeat"   — 保持连接的心跳
    - agent: 代理标识（用于前端颜色标签）
    """
    from sqlalchemy import select

    from database.db import async_session_factory

    # 检查项目是否存在
    async with async_session_factory() as db:
        result = await db.execute(
            select(Project).where(Project.project_id == project_id)
        )
        if not result.scalar_one_or_none():
            raise HTTPException(404, "项目不存在")

    async def ai_event_generator():
        async for entry in stream_ai_tokens(project_id):
            yield f"data: {json.dumps(entry, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        ai_event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


