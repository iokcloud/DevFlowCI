"""删除项目时清理磁盘与辅助数据。"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from config import DELIVERIES_DIR, MEMORY_DIR


def cleanup_delivery_artifacts(project_id: str) -> dict[str, bool]:
    """删除 deliveries 下与 project_id 关联的目录与 zip。"""
    removed: dict[str, bool] = {"delivery_dir": False, "delivery_zip": False}

    zip_path = DELIVERIES_DIR / f"{project_id}.zip"
    if zip_path.is_file():
        try:
            zip_path.unlink()
            removed["delivery_zip"] = True
        except OSError:
            pass

    proj_dir = DELIVERIES_DIR / project_id
    if proj_dir.is_dir():
        try:
            shutil.rmtree(proj_dir)
            removed["delivery_dir"] = True
        except OSError:
            pass

    return removed


def prune_human_fixes(project_id: str) -> int:
    """从 human_fixes.json 移除该项目的反馈记录。返回删除条数。"""
    fixes_file = MEMORY_DIR / "human_fixes.json"
    if not fixes_file.is_file():
        return 0
    try:
        data: dict[str, Any] = json.loads(
            fixes_file.read_text(encoding="utf-8")
        )
    except (json.JSONDecodeError, OSError):
        return 0

    fixes = data.get("fixes") or []
    kept = [f for f in fixes if f.get("project_id") != project_id]
    removed = len(fixes) - len(kept)
    if removed <= 0:
        return 0

    data["fixes"] = kept
    try:
        fixes_file.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError:
        return 0
    return removed
