"""DevFlow CI 端到端流程测试 — 自动提交→确认→追踪→报告。

用法:
    python test_flow.py

环境变量:
    TEST_FLOW_TIMEOUT   最大等待秒数（默认 900）
    TEST_FLOW_POLL_SEC  轮询间隔（默认 4）
    TEST_FLOW_HEARTBEAT 心跳输出间隔（默认 15）

前置: 服务已启动 (python main.py)，.env 中 DEEPSEEK_API_KEY 有效。
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from typing import Any

# Windows 控制台 UTF-8
os.environ["PYTHONIOENCODING"] = "utf-8"
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]

import httpx

BASE = os.getenv("TEST_FLOW_BASE", "http://127.0.0.1:8000")
TIMEOUT_S = int(os.getenv("TEST_FLOW_TIMEOUT", "900"))
POLL_SEC = float(os.getenv("TEST_FLOW_POLL_SEC", "4"))
HEARTBEAT_SEC = int(os.getenv("TEST_FLOW_HEARTBEAT", "15"))
STUCK_WARN_SEC = int(os.getenv("TEST_FLOW_STUCK_WARN", "120"))

TERMINAL = frozenset(
    {"completed", "failed", "needs_review", "cancelled", "completed_with_warnings"}
)


def _plan_module_count(data: dict[str, Any]) -> int:
    plan = data.get("plan")
    if isinstance(plan, dict):
        return len(plan.get("modules") or [])
    return 0


def _module_stats(modules: list[dict[str, Any]]) -> tuple[int, int, int]:
    passed = sum(1 for m in modules if m.get("status") == "passed")
    blocked = sum(1 for m in modules if m.get("status") == "blocked")
    coding = sum(
        1
        for m in modules
        if m.get("status") in ("coding", "analyzing", "testing", "reviewing", "auto_fixing")
    )
    return passed, blocked, coding


async def _confirm_once(
    client: httpx.AsyncClient,
    pid: str,
    status: str,
    confirmed: set[str],
) -> None:
    """每个确认点只调用一次 confirm_plan，避免重复 400。"""
    key = f"{pid}:{status}"
    if key in confirmed:
        return

    if status == "aligned":
        print("  → 自动确认需求对齐 (confirm_plan)...")
        resp = await client.post(
            f"{BASE}/api/projects/{pid}/confirm_plan",
            json={"plan_choice": "A"},
        )
        if resp.status_code == 200:
            confirmed.add(key)
            print(f"     ✅ {resp.json().get('message', resp.json())}")
        else:
            print(f"     ⚠️ HTTP {resp.status_code}: {resp.text[:200]}")

    elif status == "plan_ready":
        print("  → 自动确认执行规划 (confirm_plan)...")
        resp = await client.post(
            f"{BASE}/api/projects/{pid}/confirm_plan",
            json={},
        )
        if resp.status_code == 200:
            confirmed.add(key)
            print(f"     ✅ 进入执行: {resp.json().get('status', '')}")
        else:
            print(f"     ⚠️ HTTP {resp.status_code}: {resp.text[:200]}")


async def main() -> int:
    print("=" * 60)
    print("DevFlow CI test_flow.py")
    print(f"  超时: {TIMEOUT_S}s | 轮询: {POLL_SEC}s | 心跳: {HEARTBEAT_SEC}s")
    print("=" * 60)

    async with httpx.AsyncClient(timeout=httpx.Timeout(30, connect=10)) as client:
        # 健康检查
        try:
            health = await client.get(f"{BASE}/api/health")
            if health.status_code != 200:
                print(f"❌ 服务未就绪: HTTP {health.status_code}")
                return 1
            print(f"✅ 服务在线: {health.json()}")
        except httpx.ConnectError:
            print(f"❌ 无法连接 {BASE}，请先运行: python main.py")
            return 1

        print("\nSTEP 1: 创建测试项目...")
        resp = await client.post(
            f"{BASE}/api/projects",
            json={
                "requirement": (
                    "Write a Python function is_prime(n: int) -> bool that "
                    "checks if a number is prime. Include type hints and docstring."
                ),
                "force_new": True,
            },
        )
        if resp.status_code != 200:
            print(f"创建失败: HTTP {resp.status_code}: {resp.text}")
            return 1
        pid = resp.json()["project_id"]
        print(f"项目 ID: {pid}")

        print("\nSTEP 2: 监控工作流（LLM 阶段可能耗时数分钟，请等待心跳输出）...")
        states_seen: set[str] = set()
        confirmed: set[str] = set()
        start = time.time()
        last_heartbeat = start
        last_status = ""
        last_status_change = start
        last_progress_sig = ""

        while True:
            await asyncio.sleep(POLL_SEC)
            elapsed = int(time.time() - start)

            try:
                resp = await client.get(f"{BASE}/api/projects/{pid}")
                data = resp.json()
            except Exception as e:
                print(f"  [{elapsed:4d}s] 轮询错误: {e}")
                continue

            status = data.get("status", "?")
            modules = data.get("modules") or []
            mod_count = len(modules)
            plan_count = _plan_module_count(data) if mod_count == 0 else mod_count
            passed, blocked, coding = _module_stats(modules)
            fix_total = sum(len(m.get("auto_fix_history") or []) for m in modules)

            if status not in states_seen:
                states_seen.add(status)
                print(
                    f"  [{elapsed:4d}s] 状态变更: {status:15s} "
                    f"模块={mod_count}/{plan_count} 通过={passed} "
                    f"进行中={coding} 阻塞={blocked} 修复={fix_total}"
                )
                last_status = status
                last_status_change = time.time()

            progress_sig = f"{status}:{passed}:{blocked}:{coding}:{mod_count}"
            if progress_sig != last_progress_sig:
                last_progress_sig = progress_sig
                last_status_change = time.time()

            # 自动确认（各一次）
            if status in ("aligned", "plan_ready"):
                await _confirm_once(client, pid, status, confirmed)

            # 心跳：即使状态不变也定期输出，避免「假卡住」
            now = time.time()
            if now - last_heartbeat >= HEARTBEAT_SEC:
                last_heartbeat = now
                stuck_for = int(now - last_status_change)
                hint = ""
                if status in ("aligning", "planning", "executing", "integrating", "reviewing"):
                    hint = "（LLM 调用中，单模块最长约 10 分钟）"
                elif status == "aligned" and f"{pid}:aligned" not in confirmed or status == "plan_ready" and f"{pid}:plan_ready" not in confirmed:
                    hint = "（等待 confirm_plan）"
                print(
                    f"  [{elapsed:4d}s] ⏳ 心跳 | 状态={status} | "
                    f"模块 {passed}/{mod_count or plan_count} 完成{hint}"
                )
                if stuck_for >= STUCK_WARN_SEC:
                    print(
                        f"  ⚠️  同一进度已持续 {stuck_for}s，"
                        f"若超过 {TIMEOUT_S}s 将超时退出"
                    )

            if status in TERMINAL or status == "completed":
                print(f"\n{'=' * 60}")
                print(f"终态: {status} (耗时 {elapsed}s)")
                print(f"模块: {mod_count}  通过: {passed}  阻塞: {blocked}")
                print(f"交付: {data.get('delivery_path', 'N/A')}")
                for m in modules:
                    tr = m.get("test_result") or {}
                    print(
                        f"  [{m.get('status', '?'):10s}] {m.get('module_name', '?'):20s} "
                        f"retry={m.get('retry_count', 0)} "
                        f"test={tr.get('summary', 'none') if tr else 'none'}"
                    )
                errs = data.get("recent_error_logs") or []
                if errs:
                    print(f"\n最近错误 ({len(errs)} 条):")
                    for e in errs[-3:]:
                        print(f"  [{e.get('level')}] {e.get('message', '')[:120]}")
                if status in ("completed", "completed_with_warnings"):
                    from test_flow_quality import check_module_quality

                    ok, msg = check_module_quality(passed, blocked, mod_count)
                    if not ok:
                        print(f"❌ {msg}")
                        return 1
                    if msg:
                        print(f"⚠️ {msg}")
                return 0 if status in ("completed", "completed_with_warnings") else 1

            if elapsed > TIMEOUT_S:
                print(f"\n❌ 超时 ({TIMEOUT_S}s)，最后状态: {status}")
                print(f"   已见状态: {sorted(states_seen)}")
                print(f"   模块: {mod_count}/{plan_count} 通过={passed}")
                print("   建议: 查看服务日志 server.log 或 UI 日志面板")
                return 1

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
