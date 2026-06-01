"""DevFlow CI 端到端流程测试 — 自动提交→确认→追踪→报告。

用法:
    python test_flow.py

无需参数，自动创建简单测试项目并追踪完整工作流。
"""

import asyncio
import json
import sys
import time

import httpx

BASE = "http://127.0.0.1:8000"
TIMEOUT_S = 300  # 最多等5分钟


async def main():
    async with httpx.AsyncClient(timeout=httpx.Timeout(600, connect=10)) as client:
        # ── 1. 创建项目 ──
        print("=" * 60)
        print("STEP 1: 创建测试项目...")
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
            return
        pid = resp.json()["project_id"]
        print(f"项目ID: {pid}")

        # ── 2. 监控循环 ──
        print("\nSTEP 2: 监控工作流...")
        states_seen = set()
        start = time.time()

        while True:
            await asyncio.sleep(4)
            elapsed = int(time.time() - start)

            try:
                resp = await client.get(f"{BASE}/api/projects/{pid}")
                data = resp.json()
            except Exception as e:
                print(f"  [{elapsed}s] 轮询错误: {e}")
                continue

            status = data.get("status", "?")
            modules = data.get("modules", [])
            mod_count = len(modules)
            passed = sum(1 for m in modules if m.get("status") == "passed")
            blocked = sum(1 for m in modules if m.get("status") == "blocked")
            fix_total = sum(len(m.get("auto_fix_history", [])) for m in modules)

            if status not in states_seen:
                states_seen.add(status)
                print(
                    f"  [{elapsed:4d}s] 状态变更: {status:15s} "
                    f"模块={mod_count} 通过={passed} 阻塞={blocked} 修复尝试={fix_total}"
                )

            # 自动确认对齐
            if status == "aligned":
                print(f"  [{elapsed}s] → 自动确认对齐...")
                await client.post(
                    f"{BASE}/api/projects/{pid}/confirm_plan",
                    json={"plan_choice": "A"},
                )

            # 自动确认执行计划
            elif status == "plan_ready":
                print(f"  [{elapsed}s] → 自动确认执行计划...")
                await client.post(
                    f"{BASE}/api/projects/{pid}/confirm_plan", json={}
                )

            # 终态
            if status in ("completed", "failed", "needs_review", "cancelled"):
                print(f"\n{'='*60}")
                print(f"终态: {status} (耗时 {elapsed}s)")
                print(f"模块数: {mod_count}  阻塞数: {data.get('blocked_count',0)}")
                print(f"交付路径: {data.get('delivery_path','N/A')}")

                for m in modules:
                    c = m.get("code", "") or ""
                    t = m.get("tests", "") or ""
                    af = m.get("auto_fix_history", [])
                    tr = m.get("test_result")
                    print(
                        f"  [{m.get('status','?'):10s}] {m.get('module_name','?'):25s} "
                        f"code={len(c):4d}c test={len(t):4d}c "
                        f"retry={m.get('retry_count',0)} fix_attempts={len(af)} "
                        f"test={tr.get('summary','?') if tr else 'none'}"
                    )
                    for a in af:
                        ok = "PASS" if a.get("success") else "FAIL"
                        print(
                            f"      [{ok}] round={a.get('round')} "
                            f"strategy={a.get('strategy','?')} "
                            f"{a.get('detail','')[:80]}"
                        )
                    if c and len(c) > 20:
                        print(f"      >>> {c[:200]}")

                errs = data.get("recent_error_logs", [])
                if errs:
                    print(f"\n错误日志 ({len(errs)}条):")
                    for e in errs[-5:]:
                        print(
                            f"  [{e.get('level')}] {e.get('module_name','')}: "
                            f"{e.get('message','')[:150]}"
                        )
                return

            if elapsed > TIMEOUT_S:
                print(f"\n超时 ({TIMEOUT_S}s)，最后状态: {status}")
                print(f"模块: {mod_count} 通过={passed} 阻塞={blocked}")
                return

    print("\n测试完成。")


if __name__ == "__main__":
    asyncio.run(main())
