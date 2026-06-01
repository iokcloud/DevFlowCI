"""Shared E2E quality assertions for test_flow scripts."""

from __future__ import annotations

import os


def min_passed_modules() -> int:
    return int(os.getenv("TEST_FLOW_MIN_PASSED", "1"))


def check_module_quality(passed: int, blocked: int, mod_count: int) -> tuple[bool, str]:
    """Return (ok, message). Fails when all modules blocked or passed below minimum."""
    if mod_count == 0:
        return True, ""

    minimum = min_passed_modules()
    if passed >= minimum:
        return True, ""

    if blocked == mod_count:
        return False, (
            f"质量断言失败: {mod_count} 个模块全部 blocked，"
            f"通过={passed}（要求至少 {minimum} 个 passed）"
        )

    if passed < minimum:
        return False, (
            f"质量断言失败: 通过={passed}，阻塞={blocked}，"
            f"总数={mod_count}（要求至少 {minimum} 个 passed）"
        )

    return True, ""
