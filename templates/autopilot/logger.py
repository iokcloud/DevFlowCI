"""
{{PROJECT_NAME}} — 自治运维: 结构化日志组件

提供:
- 结构化 JSON 日志输出
- 日志级别控制（环境变量 LOG_LEVEL）
- 可选远程日志上报（配置 LOG_REMOTE_URL 后启用）
- 请求追踪（trace_id 自动注入）

使用方式:
    from autopilot.logger import get_logger
    logger = get_logger(__name__)
    logger.info("用户登录", user_id="u123", source="web")
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from typing import Any


class StructuredFormatter(logging.Formatter):
    """JSON 结构化日志格式化器。"""

    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "line": record.lineno,
        }

        # 注入额外字段（通过 extra 参数传入）
        for key in ("trace_id", "user_id", "source", "duration_ms"):
            val = getattr(record, key, None)
            if val is not None:
                log_entry[key] = val

        # 异常信息
        if record.exc_info and record.exc_info[0]:
            import traceback
            log_entry["exception"] = "".join(
                traceback.format_exception(*record.exc_info)
            )[:1000]

        return json.dumps(log_entry, ensure_ascii=False, default=str)


# ── 远程日志上报 ─────────────────────────────────────
_REMOTE_URL = os.getenv("LOG_REMOTE_URL", "")
_LOG_QUEUE: list[dict[str, Any]] = []
_MAX_QUEUE_SIZE = 100


def _flush_remote() -> None:
    """上报积压日志到远程端点。"""
    global _LOG_QUEUE
    if not _REMOTE_URL or not _LOG_QUEUE:
        return
    try:
        import urllib.request
        data = json.dumps({"logs": _LOG_QUEUE[-50:]}, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            _REMOTE_URL, data=data,
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=5)
        _LOG_QUEUE.clear()
    except Exception:
        pass  # 远程上报失败不应影响主流程


class RemoteHandler(logging.Handler):
    """远程日志处理器。"""

    def emit(self, record: logging.LogRecord) -> None:
        if not _REMOTE_URL:
            return
        _LOG_QUEUE.append({
            "level": record.levelname,
            "message": record.getMessage(),
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
        })
        if len(_LOG_QUEUE) >= 50:
            _flush_remote()


# ── 日志工厂 ─────────────────────────────────────────
_loggers_initialized = False


def get_logger(name: str) -> logging.Logger:
    """获取结构化日志记录器。

    Args:
        name: 日志记录器名称（通常为 __name__）

    Returns:
        配置好的 Logger 实例
    """
    global _loggers_initialized
    logger = logging.getLogger(name)

    if not _loggers_initialized:
        _loggers_initialized = True
        level = os.getenv("LOG_LEVEL", "INFO").upper()
        logger.setLevel(getattr(logging, level, logging.INFO))

        # 控制台处理器
        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(StructuredFormatter())
        logger.addHandler(console)

        # 远程处理器
        if _REMOTE_URL:
            logger.addHandler(RemoteHandler())

    return logger


__all__ = ["get_logger", "StructuredFormatter"]
