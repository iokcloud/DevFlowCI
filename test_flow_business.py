"""DevFlow CI 商业文档模式端到端冒烟测试。

用法:
    python test_flow_business.py

环境变量:
    TEST_FLOW_BASE              API 基址（默认 http://127.0.0.1:8000）
    TEST_FLOW_TIMEOUT           最大等待秒数（默认 900）
    TEST_FLOW_POLL_SEC          轮询间隔（默认 4）
    TEST_FLOW_HEARTBEAT         心跳输出间隔（默认 15）
    TEST_FLOW_BUSINESS_MODE     smoke（默认，含 is_prime 短路径）| full（纯商业 Phase-1）

前置: 服务已启动，.env 中 DEEPSEEK_API_KEY 有效。
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from typing import Any

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

BUSINESS_MODE = os.getenv("TEST_FLOW_BUSINESS_MODE", "smoke").strip().lower()

BUSINESS_REQUIREMENT_SMOKE = (
    "基于市场调研：银发经济市场规模增长，竞争分析显示健康管理赛道机会，"
    "消费者付费意愿提升，订阅制营收模型可行。"
    "请给出简洁商业计划；技术 MVP 仅需一个 Python 函数 is_prime(n) 验证开发能力。"
)

BUSINESS_REQUIREMENT_FULL = (
    "基于市场调研：银发经济市场规模增长，竞争分析显示健康管理赛道机会，"
    "消费者付费意愿提升，订阅制营收模型可行。"
    "请给出简洁商业计划，并聚焦第一阶段可交付的技术 MVP。"
)

BUSINESS_REQUIREMENT = (
    BUSINESS_REQUIREMENT_FULL if BUSINESS_MODE == "full" else BUSINESS_REQUIREMENT_SMOKE
)


async def _confirm_once(
    client: httpx.AsyncClient,
    pid: str,
    status: str,
    confirmed: set[str],
) -> None:
    key = f"{pid}:{status}"
    if key in confirmed:
        return

    if status == "aligned":
        print("  → 自动确认商业计划 (confirm_plan)...")
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


def _alignment_plan_type(data: dict[str, Any]) -> str:
    """从 GET /api/projects/{id} 响应读取 plan_type（字段名为 alignment）。"""
    for key in ("alignment", "alignment_result"):
        alignment = data.get(key) or {}
        if isinstance(alignment, dict):
            plan_type = alignment.get("plan_type", "")
            if plan_type:
                return str(plan_type)
    return ""


async def main() -> int:
    print("=" * 60)
    print("DevFlow CI test_flow_business.py")
    print(f"  模式: {BUSINESS_MODE} | 超时: {TIMEOUT_S}s | 轮询: {POLL_SEC}s | 心跳: {HEARTBEAT_SEC}s")
    print("=" * 60)

    async with httpx.AsyncClient(timeout=httpx.Timeout(600, connect=10)) as client:
        try:
            health = await client.get(f"{BASE}/api/health")
            if health.status_code != 200:
                print(f"❌ 服务未就绪: HTTP {health.status_code}")
                return 1
            print(f"✅ 服务在线: {health.json()}")
        except httpx.ConnectError:
            print(f"❌ 无法连接 {BASE}，请先运行: python main.py")
            return 1

        print("\nSTEP 1: 创建商业文档模式测试项目...")
        resp = await client.post(
            f"{BASE}/api/projects",
            json={
                "requirement": BUSINESS_REQUIREMENT,
                "mode": "business",
                "force_new": True,
            },
        )
        if resp.status_code != 200:
            print(f"创建失败: HTTP {resp.status_code}: {resp.text}")
            return 1
        pid = resp.json()["project_id"]
        print(f"项目 ID: {pid}")

        print("\nSTEP 2: 监控工作流（商业对齐 → 确认 → 技术规划 → 执行）...")
        states_seen: set[str] = set()
        confirmed: set[str] = set()
        start = time.time()
        last_heartbeat = start
        last_status_change = start
        last_progress_sig = ""
        business_plan_seen = False

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
            passed = sum(1 for m in modules if m.get("status") == "passed")
            blocked = sum(1 for m in modules if m.get("status") == "blocked")

            plan_type = _alignment_plan_type(data)
            if plan_type == "business":
                business_plan_seen = True

            if status not in states_seen:
                states_seen.add(status)
                print(
                    f"  [{elapsed:4d}s] 状态变更: {status:15s} "
                    f"plan_type={plan_type or '?':8s} "
                    f"模块={mod_count} 通过={passed} 阻塞={blocked}"
                )
                last_status_change = time.time()

            progress_sig = f"{status}:{passed}:{blocked}:{mod_count}:{plan_type}"
            if progress_sig != last_progress_sig:
                last_progress_sig = progress_sig
                last_status_change = time.time()

            if status in ("aligned", "plan_ready"):
                await _confirm_once(client, pid, status, confirmed)

            now = time.time()
            if now - last_heartbeat >= HEARTBEAT_SEC:
                last_heartbeat = now
                stuck_for = int(now - last_status_change)
                hint = ""
                if status in ("aligning", "planning", "executing", "integrating", "reviewing"):
                    hint = "（LLM 调用中）"
                print(
                    f"  [{elapsed:4d}s] ⏳ 心跳 | 状态={status} | "
                    f"商业计划={'是' if business_plan_seen else '否'}{hint}"
                )
                if stuck_for >= STUCK_WARN_SEC:
                    print(f"  ⚠️  同一进度已持续 {stuck_for}s")

            if status in TERMINAL:
                print(f"\n{'=' * 60}")
                print(f"终态: {status} (耗时 {elapsed}s)")
                print(f"商业计划模式: {'✅' if business_plan_seen else '❌ 未检测到'}")
                print(f"模块: {mod_count}  通过: {passed}  阻塞: {blocked}")
                print(f"交付: {data.get('delivery_path', 'N/A')}")
                if not business_plan_seen and status in ("completed", "completed_with_warnings"):
                    print("❌ 未检测到 plan_type=business，商业模式可能未生效")
                    return 1
                if status in ("completed", "completed_with_warnings"):
                    from test_flow_quality import check_module_quality

                    ok, msg = check_module_quality(passed, blocked, mod_count)
                    if not ok:
                        print(f"❌ {msg}")
                        return 1
                return 0 if status in ("completed", "completed_with_warnings") else 1

            if elapsed > TIMEOUT_S:
                print(f"\n❌ 超时 ({TIMEOUT_S}s)，最后状态: {status}")
                return 1

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
