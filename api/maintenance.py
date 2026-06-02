"""维护与运维端点。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel

from workflow.error_logger import (
    clean_resolved_logs,
    get_error_stats,
    resolve_error,
)

router = APIRouter(prefix="/api/maintenance", tags=["maintenance"])


class DeliveryCleanupRequest(BaseModel):
    project_ids: list[str] | None = None
    days_old: int = 30
    dry_run: bool = False


@router.get("/error-stats")
async def error_stats_endpoint(project_id: str | None = None) -> dict[str, Any]:
    """获取错误统计。"""
    return get_error_stats(project_id)


@router.post("/analyze-errors")
async def analyze_errors_endpoint(project_id: str | None = None) -> dict[str, Any]:
    """聚合分析错误模式。"""
    stats = get_error_stats(project_id)
    return {"stats": stats, "patterns": []}


@router.post("/clean-logs")
async def clean_logs_endpoint(days: int = 7, dry_run: bool = False) -> dict[str, Any]:
    """清理旧日志。"""
    count = clean_resolved_logs(days, dry_run)
    return {"cleaned": count, "dry_run": dry_run}


@router.get("/delivery-suggestions")
async def delivery_suggestions_endpoint() -> dict[str, Any]:
    """获取交付目录清理建议。"""
    from workflow.delivery_suggestions import get_delivery_cleanup_suggestions
    return get_delivery_cleanup_suggestions()


@router.post("/delivery-cleanup")
async def delivery_cleanup_endpoint(body: DeliveryCleanupRequest) -> dict[str, Any]:
    """清理交付目录。"""
    from workflow.delivery_suggestions import cleanup_deliveries
    result = cleanup_deliveries(
        project_ids=body.project_ids,
        days_old=body.days_old,
        dry_run=body.dry_run,
    )
    return result
