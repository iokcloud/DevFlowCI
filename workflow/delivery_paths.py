"""交付物 ZIP 路径解析。"""

from __future__ import annotations

from pathlib import Path

from config import DELIVERIES_DIR


def _latest_versioned_zip(base_dir: Path) -> Path | None:
    """在 deliveries/{project_id}/ 下找最新 vN.zip。"""
    if not base_dir.is_dir():
        return None

    version_zips: list[tuple[int, Path]] = []
    for item in base_dir.iterdir():
        if not item.is_file() or not item.name.startswith("v") or not item.suffix == ".zip":
            continue
        num = item.stem[1:]
        if num.isdigit():
            version_zips.append((int(num), item))

    if not version_zips:
        return None
    version_zips.sort(key=lambda x: x[0])
    return version_zips[-1][1]


def resolve_delivery_zip(
    project_id: str,
    delivery_path: str | None = None,
) -> Path | None:
    """解析可下载的交付 ZIP 路径。

    优先级：DB 记录的 delivery_path → 版本化 vN.zip → 旧版扁平 {id}.zip。
    """
    if delivery_path:
        stored = Path(delivery_path)
        if stored.is_file():
            return stored

    base_dir = DELIVERIES_DIR / project_id
    versioned = _latest_versioned_zip(base_dir)
    if versioned:
        return versioned

    legacy = DELIVERIES_DIR / f"{project_id}.zip"
    if legacy.is_file():
        return legacy

    return None
