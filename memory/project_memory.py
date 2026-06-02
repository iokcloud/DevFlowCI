"""项目级记忆系统。

为每个项目目录（directory）维护独立的记忆文件，
记录规划历史、模块状态和修改轨迹，
支持增量开发的上下文延续。

与 success_cases.json 通用记忆库独立维护。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone

UTC = timezone.utc
from pathlib import Path
from typing import Any

from config import PROJECT_MEMORY_DIR

# ── 数据结构 ──────────────────────────────────────────────

@dataclass
class ProjectMemory:
    """单条项目记忆记录。"""

    directory: str                          # 项目目录绝对路径
    directory_hash: str                     # 路径的 MD5 哈希（用作文件名）
    last_requirement: str = ""              # 最近一次需求
    last_plan: list[dict[str, Any]] = field(default_factory=list)
    modules: list[dict[str, Any]] = field(default_factory=list)  # [{module_name, status, description}]
    last_modified_module: str = ""          # 最近一次修改的模块名称
    last_updated: str = ""                  # ISO 时间戳
    total_sessions: int = 0                 # 累计会话数

    def to_dict(self) -> dict[str, Any]:
        return {
            "directory": self.directory,
            "directory_hash": self.directory_hash,
            "last_requirement": self.last_requirement,
            "last_plan": self.last_plan,
            "modules": self.modules,
            "last_modified_module": self.last_modified_module,
            "last_updated": self.last_updated,
            "total_sessions": self.total_sessions,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ProjectMemory:
        return cls(
            directory=d.get("directory", ""),
            directory_hash=d.get("directory_hash", ""),
            last_requirement=d.get("last_requirement", ""),
            last_plan=d.get("last_plan", []),
            modules=d.get("modules", []),
            last_modified_module=d.get("last_modified_module", ""),
            last_updated=d.get("last_updated", ""),
            total_sessions=d.get("total_sessions", 0),
        )


# ── 哈希工具 ──────────────────────────────────────────────

def hash_directory_path(directory: str) -> str:
    """对项目目录路径做 MD5 哈希，用作文件名。

    Args:
        directory: 项目目录的绝对路径

    Returns:
        16 位十六进制哈希字符串
    """
    return hashlib.md5(directory.encode("utf-8")).hexdigest()[:16]


# ── 记忆管理器 ────────────────────────────────────────────

class ProjectMemoryStore:
    """项目级记忆的持久化存储与检索。"""

    def __init__(self, base_dir: Path = PROJECT_MEMORY_DIR) -> None:
        self._base_dir = base_dir
        self._base_dir.mkdir(parents=True, exist_ok=True)

    def _file_path(self, directory: str) -> Path:
        """获取项目目录对应的记忆文件路径。"""
        hash_name = hash_directory_path(directory)
        return self._base_dir / f"{hash_name}.json"

    def load(self, directory: str) -> ProjectMemory | None:
        """加载指定目录的项目记忆。

        Args:
            directory: 项目目录的绝对路径

        Returns:
            ProjectMemory 或 None（不存在时）
        """
        file_path = self._file_path(directory)
        if not file_path.exists():
            return None
        try:
            data = json.loads(file_path.read_text(encoding="utf-8"))
            return ProjectMemory.from_dict(data)
        except (json.JSONDecodeError, KeyError):
            return None

    def save(self, memory: ProjectMemory) -> None:
        """保存项目记忆。

        Args:
            memory: 要保存的 ProjectMemory 实例
        """
        memory.last_updated = datetime.now(UTC).isoformat()
        file_path = self._file_path(memory.directory)
        file_path.write_text(
            json.dumps(memory.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def update_after_delivery(
        self,
        directory: str,
        requirement: str,
        plan: list[dict[str, Any]],
        module_results: dict[str, dict[str, Any]],
        last_modified_module: str = "",
    ) -> None:
        """一次交付完成后更新项目记忆。

        Args:
            directory: 项目目录路径
            requirement: 本次需求
            plan: PM 规划列表
            module_results: 模块执行结果 {module_name: {status, ...}}
            last_modified_module: 最近修改的模块名
        """
        existing = self.load(directory)
        if existing:
            memory = existing
            memory.total_sessions += 1
        else:
            memory = ProjectMemory(
                directory=directory,
                directory_hash=hash_directory_path(directory),
                total_sessions=1,
            )

        memory.last_requirement = requirement
        memory.last_plan = plan
        memory.last_modified_module = last_modified_module or ""

        # 更新模块状态
        existing_modules = {m["module_name"]: m for m in memory.modules}
        for m in plan:
            name = m.get("module_name", "")
            status = module_results.get(name, {}).get("status", "pending")
            existing_modules[name] = {
                "module_name": name,
                "description": m.get("description", ""),
                "status": status,
            }
        memory.modules = list(existing_modules.values())

        self.save(memory)

    def format_for_prompt(self, directory: str) -> str:
        """将项目记忆格式化为可注入 PM Agent Prompt 的文本。

        Args:
            directory: 项目目录路径

        Returns:
            格式化的记忆描述文本，注入到 PM Prompt 中
        """
        memory = self.load(directory)
        if not memory:
            return "（无历史项目记忆）"

        lines: list[str] = [
            f"## 📋 项目历史记忆（目录: {memory.directory}）",
            f"- 累计开发会话数: {memory.total_sessions}",
            f"- 最近更新: {memory.last_updated}",
            f"- 最近需求: {memory.last_requirement[:200]}",
        ]

        if memory.modules:
            lines.append("\n### 现有模块及其状态")
            for m in memory.modules:
                status_icon = {
                    "passed": "✅", "blocked": "⚠️", "failed": "❌",
                    "pending": "⏳", "error": "💥",
                }.get(m.get("status", ""), "❓")
                lines.append(
                    f"  {status_icon} [{m.get('module_name', '?')}] "
                    f"{m.get('description', '')[:80]} "
                    f"(状态: {m.get('status', 'unknown')})"
                )

        if memory.last_plan:
            lines.append(f"\n### 最近一次规划（{len(memory.last_plan)} 个模块）")
            for m in memory.last_plan[:10]:
                lines.append(
                    f"  - [{m.get('module_name', '?')}] "
                    f"{m.get('description', '')[:80]}"
                )

        if memory.last_modified_module:
            lines.append(f"\n最近修改的模块: **{memory.last_modified_module}**")

        lines.append(
            "\n⚠️ 请在上述现有基础上进行增量规划，避免重复造轮子。"
        )

        return "\n".join(lines)
