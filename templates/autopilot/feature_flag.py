"""
{{PROJECT_NAME}} — 自治运维: 特性开关管理

提供:
- 从配置文件（feature_flags.json）和环境变量读取特性开关
- 动态开关控制，无需重启
- 支持百分比灰度发布
- 支持用户/租户级别的白名单

使用方式:
    from autopilot.feature_flag import is_enabled, get_all_flags
    if is_enabled("new_search_algorithm"):
        # 新搜索算法逻辑
    else:
        # 旧逻辑
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_FLAGS_FILE = Path(__file__).parent / "feature_flags.json"
_CACHE: dict[str, dict[str, Any]] = {}
_CACHE_TIME = 0.0
_CACHE_TTL = 30.0  # 30 秒刷新一次


def _load_flags() -> dict[str, dict[str, Any]]:
    """加载特性开关配置。"""
    global _CACHE, _CACHE_TIME

    now = time.time()
    if _CACHE and (now - _CACHE_TIME) < _CACHE_TTL:
        return _CACHE

    flags: dict[str, dict[str, Any]] = {}

    # 1. 从 JSON 文件加载
    if _FLAGS_FILE.exists():
        try:
            data = json.loads(_FLAGS_FILE.read_text(encoding="utf-8"))
            flags.update(data.get("flags", {}))
        except Exception as exc:
            logger.warning("加载特性开关配置失败: %s", exc)
            pass

    # 2. 从环境变量加载（优先级更高）
    for key, val in os.environ.items():
        if key.startswith("FF_"):
            flag_name = key[3:].lower()
            flags[flag_name] = {
                "enabled": val.lower() in ("true", "1", "yes"),
                "source": "env",
            }

    _CACHE = flags
    _CACHE_TIME = now
    return flags


def is_enabled(flag_name: str, user_id: str = "") -> bool:
    """检查特性开关是否启用。

    Args:
        flag_name: 开关名称
        user_id: 可选用户ID，用于白名单/灰度

    Returns:
        True 如果特性已启用
    """
    flags = _load_flags()
    flag = flags.get(flag_name, {})

    if not flag:
        return False

    enabled = flag.get("enabled", False)
    if not enabled:
        return False

    # 白名单检查
    whitelist = flag.get("whitelist", [])
    if whitelist and user_id and user_id not in whitelist:
        return False

    # 灰度百分比
    percentage = flag.get("percentage", 100)
    if percentage < 100 and user_id:
        hash_val = hash(f"{flag_name}:{user_id}") % 100
        return hash_val < percentage

    return True


def get_all_flags() -> dict[str, bool]:
    """获取所有特性开关状态。"""
    flags = _load_flags()
    return {name: f.get("enabled", False) for name, f in flags.items()}


def set_flag(flag_name: str, enabled: bool) -> None:
    """动态设置特性开关（仅内存，重启后恢复）。"""
    flags = _load_flags()
    flags[flag_name] = {"enabled": enabled, "source": "dynamic"}


def reload_flags() -> None:
    """强制刷新开关缓存。"""
    global _CACHE_TIME
    _CACHE_TIME = 0.0
    _load_flags()


__all__ = ["is_enabled", "get_all_flags", "set_flag", "reload_flags"]
