"""反馈学习端点。"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

UTC = timezone.utc

from fastapi import APIRouter, HTTPException
from sqlalchemy import select as _sel

from config import MAX_HUMAN_FIXES, MEMORY_DIR
from database.db import async_session_factory
from database.models import Project
from api.models import FeedbackRequest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/projects", tags=["feedback"])

@router.post("/{project_id}/feedback")
async def submit_feedback(project_id: str, body: FeedbackRequest) -> dict[str, Any]:
    """提交人工修改反馈。"""
    fixes_file = MEMORY_DIR / "human_fixes.json"
    try:
        data = json.loads(fixes_file.read_text(encoding="utf-8")) if fixes_file.exists() else {"fixes": []}
    except Exception as exc:
        logger.warning("读取 human_fixes.json 失败: %s", exc)
        data = {"fixes": []}
    data["fixes"].append({
        "original_code": body.original_code[:500],
        "modified_code": body.modified_code[:500],
        "file": body.file,
        "line_range": body.line_range,
        "fix_type": body.fix_type,
        "description": body.description[:200],
        "project_id": project_id,
        "created_at": datetime.now(UTC).isoformat(),
    })
    if len(data["fixes"]) > MAX_HUMAN_FIXES:
        data["fixes"] = data["fixes"][-MAX_HUMAN_FIXES:]
    fixes_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    await push_log(project_id, "SUCCESS", f"人工反馈已记录: {body.fix_type}")
    return {"status": "recorded", "total_fixes": len(data["fixes"])}


@router.get("/{project_id}/preferences")
async def get_preference_report(project_id: str) -> dict[str, Any]:
    """生成偏好学习报告。"""
    fixes_file = MEMORY_DIR / "human_fixes.json"
    if not fixes_file.exists():
        return {"fixes": [], "summary": "暂无人工修改记录"}
    data = json.loads(fixes_file.read_text(encoding="utf-8"))
    fixes = data.get("fixes", [])
    types = {}
    for f in fixes:
        t = f.get("fix_type", "Other")
        types[t] = types.get(t, 0) + 1
    return {
        "total_fixes": len(fixes),
        "by_type": types,
        "recent_fixes": fixes[-10:],
        "summary": f"记录了 {len(fixes)} 次人工修改，最常见的类型: {max(types, key=types.get) if types else '无'} ({types.get(max(types, key=types.get), 0) if types else 0}次)",
    }


# ── 入口 ──────────────────────────────────────────────────
