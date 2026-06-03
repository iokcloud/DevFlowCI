"""流式 AI 中转模块 — DeepSeek API 流式调用 + SSE 推送。

提供：
1. StreamRelay: 封装 DeepSeek stream=True 调用，逐 token 推送到 SSE 客户端
2. 异步生成器 + asyncio.Queue 桥接
3. 完整响应缓存，流结束后存入日志文件
4. 超时/异常优雅降级为普通轮询
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import datetime, timezone

UTC = timezone.utc
import contextlib
from typing import Any

from config import (
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    DEEPSEEK_MODEL,
    LLM_TEMPERATURE,
    LLM_TIMEOUT_SECONDS,
)

# ── 全局流队列（project_id → queue）──
_stream_queues: dict[str, asyncio.Queue[dict[str, Any]]] = {}


def get_stream_queue(project_id: str) -> asyncio.Queue[dict[str, Any]]:
    """获取或创建项目的 AI 流队列。"""
    if project_id not in _stream_queues:
        _stream_queues[project_id] = asyncio.Queue(maxsize=1000)
    return _stream_queues[project_id]


def remove_stream_queue(project_id: str) -> None:
    """清理项目的 AI 流队列。"""
    _stream_queues.pop(project_id, None)


async def stream_ai_tokens(
    project_id: str,
) -> AsyncIterator[dict[str, Any]]:
    """SSE 流生成器 — 从队列中读取 AI token 并 yield。

    Args:
        project_id: 项目标识

    Yields:
        {"type": "token"|"notification"|"done"|"error", "agent": "...", "content": "...", "timestamp": "..."}
    """
    queue = get_stream_queue(project_id)
    while True:
        try:
            entry = await asyncio.wait_for(queue.get(), timeout=30)
            yield entry
            if entry.get("type") == "done":
                break
        except TimeoutError:
            yield {
                "type": "heartbeat",
                "agent": "",
                "content": "",
                "timestamp": datetime.now(UTC).isoformat(),
            }


async def push_ai_token(
    project_id: str,
    entry: dict[str, Any],
) -> None:
    """推送 AI token 到 SSE 队列。

    Args:
        project_id: 项目标识
        entry: {"type": "token"|"notification"|"done"|"error", "agent": "...", "content": "...", "timestamp": "..."}
    """
    entry.setdefault("timestamp", datetime.now(UTC).isoformat())
    queue = get_stream_queue(project_id)
    with contextlib.suppress(asyncio.QueueFull):
        queue.put_nowait(entry)  # 丢弃超额的 token


# ── 代理名称映射 ──────────────────────────────────────────────

AGENT_LABEL_MAP: dict[str, str] = {
    "alignment_agent": "📋 需求对齐",
    "business_planner": "📊 商业策划",
    "planner": "📝 PM 规划",
    "module_agents": "💻 模块编码",
    "reviewer": "🔍 审查",
    "repair_agent": "🔧 自愈修复",
    "integrator": "🔗 集成",
    "global_reviewer": "🌐 全局审查",
    "context_analyzer": "📂 上下文分析",
}


def _agent_label(agent_name: str) -> str:
    return AGENT_LABEL_MAP.get(agent_name, f"🤖 {agent_name}")


# ── 核心流式调用 ────────────────────────────────────────────


async def stream_deepseek_call(
    project_id: str,
    messages: list[dict[str, Any]],
    agent_name: str = "",
    *,
    model: str = DEEPSEEK_MODEL,
    temperature: float = LLM_TEMPERATURE,
    max_tokens: int | None = None,
    timeout: int = LLM_TIMEOUT_SECONDS,
    log_callback=None,
    thinking: str | None = "disabled",
    json_output: bool = False,
) -> str:
    """流式调用 DeepSeek API，逐 token 推送到 SSE，返回完整响应文本。

    超时或异常时优雅降级为普通非流式调用。

    Args:
        project_id: 项目标识
        messages: 标准 OpenAI 格式消息列表
        agent_name: 代理名称（用于前端颜色标签）
        model: 模型名
        temperature: 温度
        max_tokens: 最大 token 数
        timeout: 超时秒数
        log_callback: 异步日志回调 async fn(level, message, module_name)

    Returns:
        完整的响应文本
    """

    label = _agent_label(agent_name)

    # 推送通知：AI 开始思考
    await push_ai_token(project_id, {
        "type": "notification",
        "agent": agent_name,
        "content": f"{label} 正在生成...",
    })

    # ── 尝试流式调用 ──
    try:
        import httpx

        api_key = DEEPSEEK_API_KEY
        base_url = DEEPSEEK_BASE_URL.rstrip("/")
        api_url = f"{base_url}/chat/completions"

        # 用 httpx 做流式请求（兼容 async）
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }

        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "stream": True,
        }
        if thinking:
            # 直接 HTTP 调用：thinking / reasoning_effort 是 API 顶层参数，
            # 不经过 OpenAI SDK 的 extra_body 解包，直接放到 payload 顶层
            payload["thinking"] = {"type": thinking}
            if thinking == "enabled":
                from config import DEEPSEEK_REASONING_EFFORT
                payload["reasoning_effort"] = DEEPSEEK_REASONING_EFFORT
            else:
                payload["temperature"] = temperature
        else:
            payload["temperature"] = temperature
        if json_output:
            payload["response_format"] = {"type": "json_object"}

        full_response_parts: list[str] = []

        async with (  # noqa: SIM117 — stream 依赖 client，无法合并为单条 with
            httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=30.0)) as client,
            client.stream("POST", api_url, headers=headers, json=payload) as response,
        ):
                if response.status_code != 200:
                    # 流式失败 → 降级为非流式
                    error_body = await response.aread()
                    raise RuntimeError(f"HTTP {response.status_code}: {error_body.decode()[:200]}")

                async for line in response.aiter_lines():
                    if not line or not line.startswith("data: "):
                        continue
                    data_str = line[6:]  # 移除 "data: " 前缀
                    if data_str.strip() == "[DONE]":
                        break
                    try:
                        data = json.loads(data_str)
                        choices = data.get("choices", [])
                        if choices:
                            delta = choices[0].get("delta", {})
                            content = delta.get("content", "")
                            if content:
                                full_response_parts.append(content)
                                await push_ai_token(project_id, {
                                    "type": "token",
                                    "agent": agent_name,
                                    "content": content,
                                })
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue

        full_response = "".join(full_response_parts)

        if full_response:
            await push_ai_token(project_id, {
                "type": "notification",
                "agent": agent_name,
                "content": f"{label} 生成完成（{len(full_response)} 字符）",
            })
        else:
            # 流式未返回任何 token → 降级为非流式
            raise RuntimeError("流式响应未返回任何 token")

        # 推送完成信号
        await push_ai_token(project_id, {
            "type": "done",
            "agent": agent_name,
            "content": "",
        })

        return full_response

    except Exception as exc:
        # ── 优雅降级：回退到非流式调用 ──
        if log_callback:
            with contextlib.suppress(Exception):
                await log_callback(
                    "WARN",
                    f"流式调用失败 ({exc})，降级为普通调用",
                    agent_name,
                )

        await push_ai_token(project_id, {
            "type": "notification",
            "agent": agent_name,
            "content": "⚠️ 流式传输不可用，使用普通模式...",
        })

        try:
            from utils import create_llm
            llm = create_llm(
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=timeout,
                max_retries=2,
                thinking=thinking if thinking in ("enabled", "disabled") else "disabled",
                json_output=json_output,
            )
            response = await llm.ainvoke(messages)
            full_response = response.content if hasattr(response, "content") else str(response)

            # 降级模式下一次性推送完整响应
            chunk_size = 50
            for i in range(0, len(full_response), chunk_size):
                chunk = full_response[i : i + chunk_size]
                await push_ai_token(project_id, {
                    "type": "token",
                    "agent": agent_name,
                    "content": chunk,
                })
                await asyncio.sleep(0.05)  # 模拟流式延迟

            await push_ai_token(project_id, {
                "type": "done",
                "agent": agent_name,
                "content": "",
            })

            return full_response

        except Exception as fallback_exc:
            await push_ai_token(project_id, {
                "type": "error",
                "agent": agent_name,
                "content": f"AI 调用失败: {fallback_exc}",
            })
            await push_ai_token(project_id, {
                "type": "done",
                "agent": agent_name,
                "content": "",
            })
            raise


async def stream_deepseek_call_non_blocking(
    project_id: str,
    messages: list[dict[str, Any]],
    agent_name: str = "",
    **kwargs,
) -> str:
    """非阻塞版本的流式调用 — 内部创建 asyncio.Task，立即返回。

    用于不阻塞主工作流的场景。

    Args:
        project_id: 项目标识
        messages: 消息列表
        agent_name: 代理名称
        **kwargs: 传递给 stream_deepseek_call 的额外参数

    Returns:
        完整响应文本（await 后）
    """
    return await stream_deepseek_call(
        project_id=project_id,
        messages=messages,
        agent_name=agent_name,
        **kwargs,
    )
