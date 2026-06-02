"""
{{PROJECT_NAME}} — 自治运维: 健康检查模块

提供:
- /health 端点（Web 项目）或 health() 函数
- JSON 格式的系统状态报告
- 集成到 FastAPI/Flask 路由中

使用方式:
    Web:   from autopilot.health_check import router; app.include_router(router)
    CLI:   python -c "from autopilot.health_check import health; print(health())"
"""

from __future__ import annotations

import platform
import sys
from datetime import UTC, datetime
from typing import Any


def _get_uptime() -> float:
    """获取进程运行时间（秒）。"""
    try:
        import time as _time
        return _time.time() - _time.process_time()
    except Exception:
        return -1.0


def _get_memory_usage() -> dict[str, Any]:
    """获取内存使用信息。"""
    try:
        import psutil
        proc = psutil.Process()
        mem = proc.memory_info()
        return {"rss_mb": round(mem.rss / (1024 * 1024), 2), "vms_mb": round(mem.vms / (1024 * 1024), 2)}
    except ImportError:
        return {"rss_mb": -1, "vms_mb": -1}


def health() -> dict[str, Any]:
    """返回 JSON 格式的系统健康状态。

    Returns:
        {
            "status": "healthy" | "degraded" | "unhealthy",
            "timestamp": "2026-05-31T12:00:00Z",
            "version": "{{PROJECT_VERSION}}",
            "python_version": "3.12",
            "platform": "Windows-10",
            "uptime_seconds": 1234.5,
            "memory": {"rss_mb": 45.2, "vms_mb": 120.0},
            "checks": {"database": "ok", "cache": "ok"}
        }
    """
    status_data: dict[str, Any] = {
        "status": "healthy",
        "timestamp": datetime.now(UTC).isoformat(),
        "version": "{{PROJECT_VERSION}}",
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}",
        "platform": platform.platform(),
        "uptime_seconds": round(_get_uptime(), 1),
        "memory": _get_memory_usage(),
        "checks": {},
    }

    # 子组件健康检查
    try:
        from autopilot.feature_flag import get_all_flags
        flags = get_all_flags()
        status_data["feature_flags"] = len(flags)
    except Exception:
        status_data["feature_flags"] = "unavailable"

    try:
        from autopilot.self_healing import get_healing_stats
        status_data["self_healing"] = get_healing_stats()
    except Exception:
        status_data["self_healing"] = "disabled"

    return status_data


def check_database() -> str:
    """检查数据库连接（由子项目覆写）。"""
    return "disabled"


def check_cache() -> str:
    """检查缓存连接（由子项目覆写）。"""
    return "disabled"


# ── Web 路由（FastAPI 兼容）─────────────────────────
try:
    from fastapi import APIRouter
    _router = APIRouter(tags=["health"])

    @_router.get("/health", response_model=dict)
    async def health_endpoint():
        """GET /health — 健康检查端点。"""
        return health()

    @_router.get("/health/ready")
    async def readiness_endpoint():
        """GET /health/ready — K8s readiness probe。"""
        return {"status": "ready"}

    @_router.get("/health/live")
    async def liveness_endpoint():
        """GET /health/live — K8s liveness probe。"""
        return {"status": "alive"}

    router = _router
except ImportError:
    router = None  # 非 Web 项目不使用路由


__all__ = ["health", "check_database", "check_cache", "router"]
