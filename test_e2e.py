"""DevFlow CI 端到端功能测试（v0.4）。

测试内容：
1. 简单需求创建与轮询（含对齐确认）
2. 大型项目规划与执行（含对齐确认）
3. 交付物下载与验证

用法:
    python test_e2e.py

环境变量:
    TEST_E2E_BASE         服务地址（默认 http://127.0.0.1:8000）
    TEST_E2E_HEARTBEAT    心跳输出间隔秒数（默认 10）
    TEST_E2E_STUCK_WARN   卡住警告阈值秒数（默认 120）

前置: 服务已启动且 .env 中 DEEPSEEK_API_KEY 有效。
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
import zipfile
from io import BytesIO
from typing import Any

# 强制 UTF-8 输出（Windows 兼容）
os.environ["PYTHONIOENCODING"] = "utf-8"
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]

import httpx

BASE = os.getenv("TEST_E2E_BASE", "http://127.0.0.1:8000")
HEARTBEAT_SEC = int(os.getenv("TEST_E2E_HEARTBEAT", "10"))
STUCK_WARN_SEC = int(os.getenv("TEST_E2E_STUCK_WARN", "120"))
RESULTS: dict[str, dict] = {}
TERMINAL_STATUSES = frozenset(
    {"completed", "failed", "needs_review", "cancelled"}
)


def log(section: str, msg: str) -> None:
    timestamp = time.strftime("%H:%M:%S")
    print(f"[{timestamp}] [{section}] {msg}")


async def _auto_confirm(client: httpx.AsyncClient, pid: str, status: str) -> None:
    """v0.4：对齐与规划阶段自动确认。"""
    if status == "aligned":
        await client.post(
            f"{BASE}/api/projects/{pid}/confirm_plan",
            json={"plan_choice": "A"},
        )
    elif status == "plan_ready":
        await client.post(f"{BASE}/api/projects/{pid}/confirm_plan", json={})


async def _poll_project(
    client: httpx.AsyncClient,
    pid: str,
    section: str,
    max_wait: int,
    *,
    stop_at: str | None = None,
    heartbeat_sec: int = 15,
    stuck_warn_sec: int = 120,
) -> dict[str, Any]:
    """轮询项目状态，自动处理 aligned / plan_ready 确认。

    包含心跳输出和卡住警告，避免漫长的 LLM 阶段"看起来像卡死"。
    """
    states_seen: set[str] = set()
    start = time.time()
    last_heartbeat = start
    last_progress_change = start
    last_progress_sig = ""

    while time.time() - start < max_wait:
        await asyncio.sleep(3)
        resp = await client.get(f"{BASE}/api/projects/{pid}")
        assert resp.status_code == 200, f"轮询失败: {resp.status_code}"
        data = resp.json()
        status = data["status"]
        modules = data.get("modules") or []
        mod_count = len(modules)

        # 构建进度签名，用于检测是否有实际进展
        passed = sum(1 for m in modules if m.get("status") == "passed")
        blocked = sum(1 for m in modules if m.get("status") == "blocked")
        progress_sig = f"{status}:{passed}:{blocked}:{mod_count}"

        if status not in states_seen:
            states_seen.add(status)
            mod_info = f" ({mod_count} 模块)" if mod_count else ""
            log(section, f"状态变更: {status}{mod_info}")
            last_progress_change = time.time()
            last_progress_sig = progress_sig
        elif progress_sig != last_progress_sig:
            # 状态没变但模块进度有变化（如 passed 数增加）
            elapsed = int(time.time() - start)
            log(
                section,
                f"[{elapsed}s] 模块进度: {passed}/{mod_count} 通过"
                + (f", {blocked} 阻塞" if blocked else ""),
            )
            last_progress_change = time.time()
            last_progress_sig = progress_sig

        if status in ("aligned", "plan_ready"):
            log(section, f"→ 自动确认 ({status})...")
            await _auto_confirm(client, pid, status)

        # ── 心跳：即使状态不变也定期输出，避免"假卡住" ──
        now = time.time()
        if now - last_heartbeat >= heartbeat_sec:
            last_heartbeat = now
            elapsed = int(time.time() - start)
            stuck_for = int(now - last_progress_change)
            hint = ""
            if status in ("aligning", "planning", "executing", "integrating", "reviewing"):
                hint = " （LLM 调用中，单模块最长约 10 分钟）"
            elif status in ("aligned", "plan_ready"):
                hint = " （等待确认）"
            log(
                section,
                f"[{elapsed}s] ⏳ 心跳 | 状态={status}"
                + (f" | 模块 {passed}/{mod_count} 完成" if mod_count else "")
                + hint,
            )
            if stuck_for >= stuck_warn_sec:
                log(
                    section,
                    f"⚠️ 同一进度已持续 {stuck_for}s，"
                    f"若超过 {max_wait}s 将超时退出",
                )

        if stop_at and status == stop_at:
            return {
                "pid": pid,
                "passed": True,
                "states": sorted(states_seen),
                "project": data,
                "duration": round(time.time() - start, 1),
            }

        if status in TERMINAL_STATUSES:
            passed = status == "completed"
            return {
                "pid": pid,
                "passed": passed,
                "states": sorted(states_seen),
                "project": data if passed else None,
                "error": data.get("final_report", str(data)) if not passed else None,
                "duration": round(time.time() - start, 1),
            }

    return {
        "pid": pid,
        "passed": False,
        "error": "超时",
        "states": sorted(states_seen),
    }


async def test_1_simple_project() -> dict:
    """测试简单需求：回文判断函数。"""
    log("TEST-1", "=" * 50)
    log("TEST-1", "提交简单需求：写一个 is_palindrome(s) 回文判断函数")

    async with httpx.AsyncClient(timeout=httpx.Timeout(30, connect=10)) as client:
        resp = await client.post(
            f"{BASE}/api/projects",
            json={
                "requirement": (
                    "写一个函数 is_palindrome(s)，判断字符串是否回文，忽略大小写和空格"
                ),
                "force_new": True,
            },
        )
        assert resp.status_code == 200, f"创建失败: {resp.status_code}"
        pid = resp.json()["project_id"]
        log("TEST-1", f"项目已创建: {pid}")

        result = await _poll_project(client, pid, "TEST-1", max_wait=300)
        if result.get("passed"):
            log("TEST-1", f"✅ 完成！耗时 {result.get('duration', '?')}s")
        elif result.get("error") == "超时":
            log("TEST-1", "⏱ 超时")
        else:
            log("TEST-1", f"❌ 失败: {result.get('error', '')[:200]}")
        return result


async def test_2_complex_project() -> dict:
    """测试大型项目：命令行待办事项工具。"""
    log("TEST-2", "=" * 50)
    log("TEST-2", "提交大型项目需求：命令行待办事项工具")

    async with httpx.AsyncClient(timeout=httpx.Timeout(30, connect=10)) as client:
        resp = await client.post(
            f"{BASE}/api/projects",
            json={
                "requirement": (
                    "做一个命令行待办事项工具，支持添加、列出、删除任务，数据存 JSON 文件"
                ),
                "force_new": True,
            },
        )
        assert resp.status_code == 200, f"创建失败: {resp.status_code}"
        pid = resp.json()["project_id"]
        log("TEST-2", f"项目已创建: {pid}")

        # 先等到 plan_ready 并确认，再等到 completed
        plan_result = await _poll_project(
            client, pid, "TEST-2", max_wait=180, stop_at="plan_ready"
        )
        if not plan_result.get("passed"):
            log("TEST-2", f"❌ 规划阶段失败: {plan_result.get('error', '超时')}")
            return plan_result

        plan = plan_result.get("project", {}).get("plan", {})
        modules = plan.get("modules", [])
        log("TEST-2", f"规划完成！拆解为 {len(modules)} 个子任务")
        for m in modules:
            log(
                "TEST-2",
                f"  [{m.get('module_name')}] {m.get('description', '')[:60]}",
            )

        log("TEST-2", "确认规划，继续执行...")
        result = await _poll_project(client, pid, "TEST-2", max_wait=600)
        if result.get("passed"):
            log("TEST-2", f"✅ 完成！耗时 {result.get('duration', '?')}s")
        elif result.get("error") == "超时":
            log("TEST-2", "⏱ 超时")
        else:
            log("TEST-2", "❌ 执行失败")
        return result


async def test_3_download_and_verify(pids: list[str]) -> dict:
    """下载交付物并验证。"""
    log("TEST-3", "=" * 50)
    results: dict[str, dict] = {}

    async with httpx.AsyncClient(timeout=30) as client:
        for pid in pids:
            log("TEST-3", f"下载 {pid}.zip ...")
            resp = await client.get(f"{BASE}/api/projects/{pid}/download")

            if resp.status_code != 200:
                log("TEST-3", f"❌ 下载失败: {resp.status_code}")
                results[pid] = {"downloadable": False}
                continue

            try:
                with zipfile.ZipFile(BytesIO(resp.content)) as zf:
                    files = zf.namelist()
                    log("TEST-3", f"  ZIP 包含 {len(files)} 个文件")
                    has_readme = any("README" in f.upper() for f in files)
                    py_files = [f for f in files if f.endswith(".py")]
                    results[pid] = {
                        "downloadable": True,
                        "file_count": len(files),
                        "has_readme": has_readme,
                        "py_files": py_files,
                    }
                    log(
                        "TEST-3",
                        f"  README: {'✅' if has_readme else '❌'}, "
                        f"Python: {len(py_files)} 个",
                    )
            except Exception as e:
                log("TEST-3", f"❌ ZIP 解压失败: {e}")
                results[pid] = {"downloadable": False, "error": str(e)}

    return results


async def main() -> None:
    print()
    print("╔══════════════════════════════════════════════╗")
    print("║   DevFlow CI 端到端功能测试 (v0.4)           ║")
    print("╚══════════════════════════════════════════════╝")
    print()
    print(f"  心跳: {HEARTBEAT_SEC}s | 卡住警告: {STUCK_WARN_SEC}s")
    print()

    async with httpx.AsyncClient(timeout=5) as client:
        resp = await client.get(f"{BASE}/api/health")
        if resp.status_code != 200:
            print(f"❌ 服务未就绪: {resp.status_code}")
            sys.exit(1)
        print(f"✅ 服务在线: {resp.json()}")

    log("MAIN", "并行提交测试1和测试2...")
    r1, r2 = await asyncio.gather(
        test_1_simple_project(),
        test_2_complex_project(),
    )
    RESULTS["test_simple"] = r1
    RESULTS["test_complex"] = r2

    pids = [r["pid"] for r in (r1, r2) if r.get("passed")]
    r3 = await test_3_download_and_verify(pids) if pids else {}
    RESULTS["test_download"] = r3

    print()
    print("╔══════════════════════════════════════════════╗")
    print("║   📊 测试报告                               ║")
    print("╚══════════════════════════════════════════════╝")
    print()

    for name, result in RESULTS.items():
        if name in ("test_simple", "test_complex"):
            label = "简单需求" if name == "test_simple" else "大型项目"
            status = "✅ PASS" if result.get("passed") else "❌ FAIL"
            print(f"  {label}: {status}")
            print(f"    项目ID: {result.get('pid', 'N/A')}")
            print(f"    经历状态: {result.get('states', [])}")
            if result.get("duration"):
                print(f"    耗时: {result['duration']}s")
            if result.get("error") and not result.get("passed"):
                print(f"    错误: {str(result['error'])[:200]}")
        elif name == "test_download":
            print("  下载验证:")
            for pid, r in result.items():
                if r.get("downloadable"):
                    print(f"    {pid}: ✅ ({r['file_count']} 文件)")
                else:
                    print(f"    {pid}: ❌")

    passed = sum(1 for k, r in RESULTS.items() if k != "test_download" and r.get("passed"))
    print(f"\n  总计: {passed}/2 主测试通过")
    print()


if __name__ == "__main__":
    asyncio.run(main())
