#!/usr/bin/env python3
"""分析项目 blocked 模块 — 从 API 或 deliveries 目录读取失败原因。

用法:
    python scripts/analyze_blocked.py <project_id>
    python scripts/analyze_blocked.py --latest
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

import httpx

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DELIVERIES = PROJECT_ROOT / "deliveries"
BASE = os.getenv("TEST_FLOW_BASE", "http://127.0.0.1:8000")


async def fetch_project(project_id: str) -> dict:
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(f"{BASE}/api/projects/{project_id}")
        resp.raise_for_status()
        return resp.json()


def summarize(data: dict) -> str:
    lines = [
        f"项目: {data.get('project_id')}",
        f"状态: {data.get('status')}",
        f"需求: {(data.get('requirement') or '')[:120]}...",
        "",
    ]
    modules = data.get("modules") or []
    blocked = [m for m in modules if m.get("status") == "blocked"]
    passed = [m for m in modules if m.get("status") == "passed"]
    lines.append(f"模块: {len(modules)}  通过: {len(passed)}  阻塞: {len(blocked)}")
    lines.append("")

    for m in blocked:
        lines.append(f"## blocked: {m.get('module_name')}")
        lines.append(f"  retry: {m.get('retry_count', 0)}")
        reason = m.get("failure_reason") or ""
        if reason:
            lines.append(f"  原因: {reason[:300]}")
        review = m.get("review_result")
        if review:
            try:
                rv = json.loads(review) if isinstance(review, str) else review
                issues = rv.get("issues") or []
                if issues:
                    lines.append(f"  审查: {'; '.join(str(i) for i in issues[:3])}")
            except (json.JSONDecodeError, TypeError):
                pass
        lines.append("")

    errs = data.get("recent_error_logs") or []
    if errs:
        lines.append("## 最近 ERROR/WARN")
        for e in errs[-5:]:
            lines.append(f"  [{e.get('level')}] {e.get('module_name', '')}: {e.get('message', '')[:100]}")
    return "\n".join(lines)


def latest_project_id() -> str | None:
    if not DELIVERIES.exists():
        return None
    dirs = sorted(DELIVERIES.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
    return dirs[0].name if dirs else None


async def main() -> int:
    parser = argparse.ArgumentParser(description="分析 blocked 模块")
    parser.add_argument("project_id", nargs="?", help="项目 ID")
    parser.add_argument("--latest", action="store_true", help="分析 deliveries 下最新项目")
    args = parser.parse_args()

    pid = args.project_id
    if args.latest or not pid:
        pid = latest_project_id()
        if not pid:
            print("未找到项目 ID")
            return 1

    try:
        data = await fetch_project(pid)
    except Exception as exc:
        print(f"无法获取项目 {pid}: {exc}")
        print("请确认服务已启动: python main.py")
        return 1

    print(summarize(data))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
