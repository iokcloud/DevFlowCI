"""扫描 deliveries 目录并给出可安全删除的交付物建议。"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from config import ARCHIVE_DIR_NAME, DELIVERIES_DIR, MAX_VERSIONS_KEPT

_KIND_LABELS: dict[str, str] = {
    "orphan_dir": "孤儿目录",
    "orphan_zip": "孤儿 ZIP",
    "legacy_zip": "旧版扁平 ZIP",
    "legacy_root": "版本化前的根层残留",
    "stale_version_dir": "应归档的旧版本目录",
    "stale_version_zip": "超保留窗口的版本 ZIP",
    "archive_zip": "归档 ZIP",
}


def _version_numbers(base_dir: Path) -> list[int]:
    nums: list[int] = []
    if not base_dir.is_dir():
        return nums
    for item in base_dir.iterdir():
        if item.is_dir() and item.name.startswith("v") and item.name[1:].isdigit():
            nums.append(int(item.name[1:]))
    return sorted(nums)


def _path_size(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        try:
            return path.stat().st_size
        except OSError:
            return 0
    total = 0
    for f in path.rglob("*"):
        if f.is_file():
            try:
                total += f.stat().st_size
            except OSError:
                pass
    return total


def _rel_path(path: Path) -> str:
    return path.relative_to(DELIVERIES_DIR).as_posix()


def _suggestion(
    *,
    kind: str,
    path: Path,
    reason: str,
    project_id: str | None,
) -> dict[str, Any]:
    return {
        "id": _rel_path(path),
        "kind": kind,
        "kind_label": _KIND_LABELS.get(kind, kind),
        "path": _rel_path(path),
        "project_id": project_id,
        "reason": reason,
        "size_bytes": _path_size(path),
    }


def scan_delivery_cleanup_suggestions(
    known_project_ids: set[str],
) -> dict[str, Any]:
    """扫描 deliveries，返回可删除项建议列表。"""
    suggestions: list[dict[str, Any]] = []

    if not DELIVERIES_DIR.is_dir():
        return {
            "suggestions": [],
            "total_size_bytes": 0,
            "count": 0,
        }

    for item in sorted(DELIVERIES_DIR.iterdir(), key=lambda p: p.name):
        if item.is_file() and item.suffix == ".zip":
            pid = item.stem
            if pid not in known_project_ids:
                suggestions.append(
                    _suggestion(
                        kind="orphan_zip",
                        path=item,
                        reason="数据库中已无此项目记录",
                        project_id=pid,
                    )
                )
            continue

        if not item.is_dir() or item.name == ARCHIVE_DIR_NAME:
            continue

        pid = item.name
        if pid not in known_project_ids:
            suggestions.append(
                _suggestion(
                    kind="orphan_dir",
                    path=item,
                    reason="数据库中已无此项目记录",
                    project_id=pid,
                )
            )
            continue

        _scan_known_project_dir(pid, item, suggestions)

    total = sum(s["size_bytes"] for s in suggestions)
    return {
        "suggestions": suggestions,
        "total_size_bytes": total,
        "count": len(suggestions),
    }


def _scan_known_project_dir(
    pid: str,
    base_dir: Path,
    suggestions: list[dict[str, Any]],
) -> None:
    versions = _version_numbers(base_dir)
    latest = versions[-1] if versions else 0
    has_versioned = bool(versions)
    keep_from = latest - MAX_VERSIONS_KEPT + 1 if latest else 0

    legacy_zip = DELIVERIES_DIR / f"{pid}.zip"
    if legacy_zip.is_file() and has_versioned:
        suggestions.append(
            _suggestion(
                kind="legacy_zip",
                path=legacy_zip,
                reason="项目已使用版本化目录，根目录扁平 ZIP 已不再需要",
                project_id=pid,
            )
        )

    reserved = {ARCHIVE_DIR_NAME}
    for item in base_dir.iterdir():
        name = item.name
        if name in reserved:
            continue
        if item.is_dir() and name.startswith("v") and name[1:].isdigit():
            continue
        if has_versioned:
            suggestions.append(
                _suggestion(
                    kind="legacy_root",
                    path=item,
                    reason="版本化交付后根层的旧文件/目录残留",
                    project_id=pid,
                )
            )
        continue

    for num in versions:
        if num >= keep_from:
            continue
        vdir = base_dir / f"v{num}"
        if vdir.is_dir():
            suggestions.append(
                _suggestion(
                    kind="stale_version_dir",
                    path=vdir,
                    reason=(
                        f"版本 v{num} 超出保留窗口（当前最新 v{latest}，"
                        f"保留最近 {MAX_VERSIONS_KEPT} 个）"
                    ),
                    project_id=pid,
                )
            )

    for item in base_dir.iterdir():
        if not item.is_file() or not item.name.startswith("v") or item.suffix != ".zip":
            continue
        num_str = item.stem[1:]
        if not num_str.isdigit():
            continue
        num = int(num_str)
        if num >= keep_from:
            continue
        suggestions.append(
            _suggestion(
                kind="stale_version_zip",
                path=item,
                reason=(
                    f"版本 v{num}.zip 超出保留窗口（当前最新 v{latest}，"
                    f"保留最近 {MAX_VERSIONS_KEPT} 个）"
                ),
                project_id=pid,
            )
        )

    archive_dir = base_dir / ARCHIVE_DIR_NAME
    if archive_dir.is_dir():
        for z in sorted(archive_dir.glob("v*.zip")):
            num_str = z.stem[1:]
            if not num_str.isdigit():
                continue
            suggestions.append(
                _suggestion(
                    kind="archive_zip",
                    path=z,
                    reason="已归档的历史版本，删除后无法通过界面回滚到该版本",
                    project_id=pid,
                )
            )


def apply_delivery_cleanup(
    paths: list[str],
    known_project_ids: set[str],
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """按建议路径删除交付物；仅允许当前扫描结果中的路径。"""
    scan = scan_delivery_cleanup_suggestions(known_project_ids)
    allowed = {s["path"] for s in scan["suggestions"]}

    deleted: list[str] = []
    skipped: list[dict[str, str]] = []
    freed_bytes = 0

    for rel in paths:
        rel_norm = rel.replace("\\", "/").strip("/")
        if rel_norm not in allowed:
            skipped.append({"path": rel, "reason": "不在可删除建议列表中"})
            continue

        target = (DELIVERIES_DIR / rel_norm).resolve()
        root = DELIVERIES_DIR.resolve()
        try:
            target.relative_to(root)
        except ValueError:
            skipped.append({"path": rel, "reason": "路径非法"})
            continue

        if not target.exists():
            skipped.append({"path": rel_norm, "reason": "路径不存在"})
            continue

        size = _path_size(target)
        if dry_run:
            deleted.append(rel_norm)
            freed_bytes += size
            continue

        try:
            if target.is_file():
                target.unlink()
            else:
                shutil.rmtree(target)
            deleted.append(rel_norm)
            freed_bytes += size
        except OSError as exc:
            skipped.append({"path": rel_norm, "reason": str(exc)})

    return {
        "dry_run": dry_run,
        "deleted": deleted,
        "deleted_count": len(deleted),
        "freed_bytes": freed_bytes,
        "skipped": skipped,
    }
