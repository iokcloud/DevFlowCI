"""健康检查端点。"""

from __future__ import annotations

from fastapi import APIRouter

from memory.case_store import CaseStore

router = APIRouter(tags=["health"])

# 全局单例引用（由 main.py 设置）
_case_store: CaseStore | None = None


def set_case_store(store: CaseStore) -> None:
    global _case_store
    _case_store = store


@router.get("/api/health")
async def health() -> dict[str, str]:
    """健康检查。"""
    return {
        "status": "ok",
        "cases_count": str(_case_store.count if _case_store else 0),
        "api_version": "0.4.9",
    }
