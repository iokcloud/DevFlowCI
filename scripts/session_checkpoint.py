#!/usr/bin/env python3
"""Refresh HANDOFF.md and CURRENT_STATUS auto-block before a new Cursor chat."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from datetime import datetime, timezone

UTC = timezone.utc
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SESSIONS_DIR = PROJECT_ROOT / "sessions"
STATUS_PATH = PROJECT_ROOT / "CURRENT_STATUS.md"
HANDOFF_PATH = PROJECT_ROOT / "HANDOFF.md"
MARKER_START = "<!-- AUTO-CHECKPOINT:START -->"
MARKER_END = "<!-- AUTO-CHECKPOINT:END -->"


def _run(cmd: list[str]) -> str:
    try:
        r = subprocess.run(
            cmd,
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return (r.stdout or "").strip()
    except OSError:
        return ""


def git_info() -> dict:
    if not (PROJECT_ROOT / ".git").is_dir():
        return {
            "branch": "(no git)",
            "status": "(not a git repo)",
            "log": "(n/a)",
            "dirty": False,
        }
    branch = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"]) or "?"
    status = _run(["git", "status", "--short"])
    log = _run(["git", "log", "-5", "--oneline"])
    dirty = bool(status)
    return {
        "branch": branch,
        "status": status or "(clean)",
        "log": log or "(no commits)",
        "dirty": dirty,
    }


def resolve_session_id(explicit: str | None) -> str:
    if explicit:
        return explicit
    today = datetime.now(UTC).strftime("%Y%m%d")
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    existing = sorted(
        p.name
        for p in SESSIONS_DIR.iterdir()
        if p.is_dir() and re.match(rf"^{today}-\d+$", p.name)
    )
    if existing:
        return existing[-1]
    count = sum(
        1 for p in SESSIONS_DIR.iterdir()
        if p.is_dir() and p.name.startswith(f"{today}-")
    )
    return f"{today}-{count + 1:02d}"


def ensure_summary(session_id: str, branch: str) -> Path:
    d = SESSIONS_DIR / session_id
    d.mkdir(parents=True, exist_ok=True)
    summary = d / "summary.md"
    if not summary.exists():
        now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M")
        summary.write_text(
            f"# 会话总结 — {session_id}\n\n"
            f"> 日期：{now}\n> 分支：{branch}\n\n"
            "## 本次目标\n\n- （待填写）\n\n"
            "## 已完成\n\n- \n\n"
            "## 下一步（给下一 chat）\n\n1. \n",
            encoding="utf-8",
        )
    return d


def update_status_auto_block(block: str) -> None:
    if not STATUS_PATH.is_file():
        STATUS_PATH.write_text(block + "\n", encoding="utf-8")
        return
    content = STATUS_PATH.read_text(encoding="utf-8")
    pattern = re.compile(
        re.escape(MARKER_START) + r".*?" + re.escape(MARKER_END),
        re.DOTALL,
    )
    if pattern.search(content):
        content = pattern.sub(block, content)
    else:
        content = content.rstrip() + "\n\n" + block + "\n"
    STATUS_PATH.write_text(content, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="DevFlow session checkpoint")
    parser.add_argument("--next-goal", default="", help="Next chat single goal")
    parser.add_argument("--message", default="", help="Optional note")
    parser.add_argument("--session-id", default="", help="Explicit session id")
    args = parser.parse_args()

    g = git_info()
    sid = resolve_session_id(args.session_id or None)
    session_dir = ensure_summary(sid, g["branch"])
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
    dirty_label = "有未提交改动" if g["dirty"] else "干净"
    goal = args.next_goal or "（运行 checkpoint 时加 --next-goal 填写）"
    uncommitted = g["status"] if g["dirty"] else "(无)"
    log_top3 = "\n".join(g["log"].splitlines()[:3])

    checkpoint = (
        f"# Checkpoint — {sid}\n\n"
        f"> 生成时间: {now}\n> 分支: {g['branch']}\n> 工作区: {dirty_label}\n\n"
        f"## 备注\n\n{args.message}\n\n"
        f"## 下一 Chat 建议目标\n\n{args.next_goal}\n\n"
        f"## Git status\n\n```\n{g['status']}\n```\n\n"
        f"## 最近提交\n\n```\n{g['log']}\n```\n"
    )
    (session_dir / "checkpoint.md").write_text(checkpoint, encoding="utf-8")

    auto_block = (
        f"{MARKER_START}\n"
        f"> **自动 checkpoint** — {now} · 会话 `{sid}` · "
        f"分支 `{g['branch']}` · 工作区 **{dirty_label}**\n\n"
        f"**下一 Chat 目标（建议）**：{goal}\n\n"
        f"**快速入口**：`HANDOFF.md` · `sessions/{sid}/checkpoint.md` · "
        f"`sessions/{sid}/summary.md`\n\n"
        f"**Git 摘要**：\n```\n{log_top3}\n```\n\n"
        f"**未提交**：\n```\n{uncommitted}\n```\n"
        f"{MARKER_END}"
    )
    update_status_auto_block(auto_block)

    handoff = f"""# HANDOFF — 新 Chat 接续入口

> 自动生成于 {now} · 会话 `{sid}`

## 新 Chat 起手式（复制整块到新对话第一条）

````markdown
## 接续 DevFlowCI

- 仓库: `{PROJECT_ROOT}`
- 分支: `{g['branch']}` · 工作区: **{dirty_label}**
- 请先读: `HANDOFF.md` → `CURRENT_STATUS.md` → `sessions/{sid}/summary.md`

### 本 Chat 唯一目标
{goal}

### 不要重复做
- 未明确要求不要 git commit
- 暂缓项见 CURRENT_STATUS.md

### 关键路径
- 迭代: main.py /iterate /finalize
- 文档: workflow/document_sync.py
- 前端: static/app.js, static/ux-states.js

请先 git status，再开始。
````

## Git 最近提交

```
{g['log']}
```

## 未提交文件

```
{uncommitted}
```

## 会话文件

- `sessions/{sid}/checkpoint.md`
- `sessions/{sid}/summary.md`（请手工补全总结）

详见 `docs/MEMORY_INDEX.md`。
"""
    HANDOFF_PATH.write_text(handoff, encoding="utf-8")

    print("")
    print("DevFlow session checkpoint")
    print(f"  Session:  sessions/{sid}")
    print("  HANDOFF:  HANDOFF.md")
    print("  Status:   CURRENT_STATUS.md (auto block)")
    print("")
    print("Open a NEW chat and paste the block from HANDOFF.md")
    if args.next_goal:
        print(f"Goal: {args.next_goal}")
    print("")
    return 0


if __name__ == "__main__":
    sys.exit(main())
