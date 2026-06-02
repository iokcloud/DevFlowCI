"""统一日志配置 — structlog + logging 双后端。

开发：彩色控制台 | 生产：JSON 行文件 | SSE 推送 | SQLite 持久化
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
from typing import Any

import structlog

from config import LOG_LEVEL, PROJECT_ROOT


def configure_logging(*, json_output: bool = False) -> None:
    """配置全局日志系统。

    Args:
        json_output: True 输出 JSON（生产），False 彩色控制台（开发）
    """
    # ── 标准 logging ──
    root = logging.getLogger()
    root.setLevel(getattr(logging, LOG_LEVEL.upper(), logging.INFO))
    root.handlers.clear()

    # 控制台
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.DEBUG)
    if json_output:
        console.setFormatter(logging.Formatter("%(message)s"))
    else:
        console.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)-5s] %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        ))
    root.addHandler(console)

    # 文件轮转
    log_file = PROJECT_ROOT / "server.log"
    file_handler = logging.handlers.RotatingFileHandler(
        str(log_file), maxBytes=10 * 1024 * 1024, backupCount=5,
    )
    file_handler.setFormatter(logging.Formatter(
        '{"time": "%(asctime)s", "level": "%(levelname)s", "logger": "%(name)s", "message": %(message)s}',
        datefmt="%Y-%m-%dT%H:%M:%S",
    ))
    file_handler.setLevel(logging.INFO)
    root.addHandler(file_handler)

    # ── structlog ──
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer()
            if not json_output
            else structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """获取绑定了 structlog 上下文的 logger。"""
    return structlog.get_logger(name or __name__)
