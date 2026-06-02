"""文件系统浏览端点。"""

from __future__ import annotations

import os
import platform
import string as _string
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/fs", tags=["filesystem"])


@router.get("/drives")
async def list_drives() -> dict[str, Any]:
    """列出可用盘符（Windows）或根目录（Unix）。"""
    system = platform.system()
    if system == "Windows":
        drives = []
        for letter in _string.ascii_uppercase:
            root = f"{letter}:\\"
            if os.path.exists(root):
                drives.append({"name": root, "path": root, "type": "drive"})
        return {"drives": drives, "separator": "\\", "system": "Windows"}
    else:
        root = Path("/")
        try:
            entries = [
                {"name": p.name, "path": str(p), "type": "dir"}
                for p in sorted(root.iterdir())
                if p.is_dir() and not p.name.startswith(".")
            ]
            return {"drives": entries, "separator": "/", "system": system}
        except PermissionError:
            return {"drives": [{"name": "/", "path": "/", "type": "dir"}], "separator": "/", "system": system}


@router.get("/browse")
async def browse_directory(path: str = "") -> dict[str, Any]:
    """浏览指定路径下的子目录。GET /api/fs/browse?path=C:\\Users"""
    if not path:
        return await list_drives()
    p = Path(path)
    if not p.exists():
        raise HTTPException(404, f"路径不存在: {path}")
    if not p.is_dir():
        raise HTTPException(400, f"路径不是目录: {path}")
    try:
        entries = [
            {"name": item.name, "path": str(item), "type": "dir"}
            for item in sorted(p.iterdir())
            if item.is_dir() and not item.name.startswith(".")
        ]
        parent = str(p.parent) if p.parent != p else None
        return {"current": str(p), "parent": parent, "entries": entries}
    except PermissionError as err:
        raise HTTPException(403, f"没有权限访问: {path}") from err


@router.get("/quick-access")
async def quick_access() -> dict[str, Any]:
    """返回用户的常用快捷路径（桌面、文档、下载等）。"""
    home = Path.home()
    entries = [
        {"name": "此电脑", "path": "", "icon": "\U0001f4bb", "type": "root"},
        {"name": home.name or str(home), "path": str(home), "icon": "\U0001f464", "type": "home"},
    ]
    quick_dirs = [
        ("桌面", home / "Desktop"),
        ("文档", home / "Documents"),
        ("下载", home / "Downloads"),
        ("图片", home / "Pictures"),
        ("音乐", home / "Music"),
        ("视频", home / "Videos"),
        ("OneDrive", home / "OneDrive"),
    ]
    for name, p in quick_dirs:
        if p.exists() and p.is_dir():
            entries.append({"name": name, "path": str(p), "icon": "\U0001f4c1", "type": "quick"})
    return {"entries": entries}
