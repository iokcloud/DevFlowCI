"""数据模型定义。

- Project: 一个用户需求对应一个项目
- ModuleTask: 项目下的子模块任务
- ProjectLog: 实时日志记录
"""

from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, relationship

from database.db import Base


# ── 枚举类型 ──────────────────────────────────────────────

class ProjectStatus(str, enum.Enum):
    """项目整体状态。"""
    CREATED = "created"            # 已创建，等待需求对齐
    ALIGNING = "aligning"          # 需求对齐分析中
    ALIGNED = "aligned"            # 需求对齐完成，等待用户确认
    PLANNING = "planning"          # PM 正在规划中
    PLAN_READY = "plan_ready"      # 规划完成，等待用户确认
    EXECUTING = "executing"        # 执行中
    INTEGRATING = "integrating"    # 集成中
    REVIEWING = "reviewing"        # 全局审查中
    COMPLETED = "completed"        # 完成（可能包含 blocked 模块）
    FAILED = "failed"              # 失败
    NEEDS_REVIEW = "needs_review"  # 需要人工介入
    CANCELLED = "cancelled"        # 用户手动终止


class ModuleStatus(str, enum.Enum):
    """模块任务状态。"""
    PENDING = "pending"            # 等待执行
    ANALYZING = "analyzing"        # 分析中
    CODING = "coding"              # 编码中
    TESTING = "testing"            # 测试中
    REVIEWING = "reviewing"        # 审查中
    AUTO_FIXING = "auto_fixing"    # 系统自动修复中
    PASSED = "passed"              # 审查通过
    BLOCKED = "blocked"            # 已阻塞（超过最大重试，生成占位文件）
    FAILED = "failed"              # 失败
    SKIPPED = "skipped"            # 跳过（依赖失败）


class ErrorStatus(str, enum.Enum):
    """错误日志状态。"""
    OPEN = "open"                  # 待处理
    RESOLVED = "resolved"          # 已修复


# ── ORM 模型 ──────────────────────────────────────────────

class Project(Base):
    """用户项目主记录。"""

    __tablename__ = "projects"

    id: Mapped[int] = Column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = Column(
        String(64), unique=True, nullable=False, index=True
    )
    requirement: Mapped[str] = Column(Text, nullable=False)
    directory: Mapped[Optional[str]] = Column(String(512), nullable=True)
    status: Mapped[ProjectStatus] = Column(
        Enum(ProjectStatus), nullable=False, default=ProjectStatus.CREATED
    )
    plan_json: Mapped[Optional[str]] = Column(Text, nullable=True)
    alignment_json: Mapped[Optional[str]] = Column(Text, nullable=True)  # 需求对齐分析结果 JSON
    final_report: Mapped[Optional[str]] = Column(Text, nullable=True)
    delivery_path: Mapped[Optional[str]] = Column(String(512), nullable=True)
    test_report_path: Mapped[Optional[str]] = Column(String(512), nullable=True)
    blocked_count: Mapped[int] = Column(Integer, default=0)

    created_at: Mapped[datetime] = Column(
        DateTime, default=lambda: datetime.now(timezone.utc), nullable=False
    )
    updated_at: Mapped[datetime] = Column(
        DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False
    )

    # 关联
    modules: Mapped[list["ModuleTask"]] = relationship(
        "ModuleTask", back_populates="project", cascade="all, delete-orphan"
    )
    logs: Mapped[list["ProjectLog"]] = relationship(
        "ProjectLog", back_populates="project", cascade="all, delete-orphan"
    )


class ModuleTask(Base):
    """项目下的子模块任务。"""

    __tablename__ = "module_tasks"

    id: Mapped[int] = Column(Integer, primary_key=True, autoincrement=True)
    project_id_fk: Mapped[int] = Column(
        Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )

    module_name: Mapped[str] = Column(String(128), nullable=False)
    description: Mapped[str] = Column(Text, nullable=False)
    dependencies: Mapped[str] = Column(Text, default="[]")  # JSON list
    module_type: Mapped[str] = Column(String(64), default="backend")

    status: Mapped[ModuleStatus] = Column(
        Enum(ModuleStatus), nullable=False, default=ModuleStatus.PENDING
    )
    retry_count: Mapped[int] = Column(Integer, default=0)
    failure_reason: Mapped[Optional[str]] = Column(Text, nullable=True)

    spec: Mapped[Optional[str]] = Column(Text, nullable=True)
    code: Mapped[Optional[str]] = Column(Text, nullable=True)
    tests: Mapped[Optional[str]] = Column(Text, nullable=True)
    review_result: Mapped[Optional[str]] = Column(Text, nullable=True)
    auto_fix_history: Mapped[Optional[str]] = Column(Text, nullable=True)  # JSON: [{strategy, success, detail, ...}]
    test_result: Mapped[Optional[str]] = Column(Text, nullable=True)       # JSON: TestResult.to_dict()

    created_at: Mapped[datetime] = Column(
        DateTime, default=lambda: datetime.now(timezone.utc), nullable=False
    )
    updated_at: Mapped[datetime] = Column(
        DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False
    )

    # 关联
    project: Mapped["Project"] = relationship("Project", back_populates="modules")


class ProjectLog(Base):
    """项目实时日志。"""

    __tablename__ = "project_logs"

    id: Mapped[int] = Column(Integer, primary_key=True, autoincrement=True)
    project_id_fk: Mapped[int] = Column(
        Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    level: Mapped[str] = Column(
        String(16), default="INFO"
    )  # INFO, WARN, ERROR, SUCCESS
    message: Mapped[str] = Column(Text, nullable=False)
    module_name: Mapped[Optional[str]] = Column(String(128), nullable=True)
    timestamp: Mapped[datetime] = Column(
        DateTime, default=lambda: datetime.now(timezone.utc), nullable=False
    )

    # 关联
    project: Mapped["Project"] = relationship("Project", back_populates="logs")


class ErrorLog(Base):
    """全局错误日志 — 用于聚合分析、自动修复和清理。

    字段：
    - trace_id: 跨模块追踪标识（同一工作流异常绑定同一 trace_id）
    - status: open / resolved，自动修复成功后标记为 resolved
    """

    __tablename__ = "error_logs"

    id: Mapped[int] = Column(Integer, primary_key=True, autoincrement=True)
    trace_id: Mapped[str] = Column(
        String(64), nullable=False, index=True
    )
    project_id: Mapped[Optional[str]] = Column(
        String(64), nullable=True, index=True
    )
    module_name: Mapped[Optional[str]] = Column(String(128), nullable=True)
    error_type: Mapped[Optional[str]] = Column(String(64), nullable=True)
    message: Mapped[str] = Column(Text, nullable=False)
    stacktrace: Mapped[Optional[str]] = Column(Text, nullable=True)
    status: Mapped[ErrorStatus] = Column(
        Enum(ErrorStatus), nullable=False, default=ErrorStatus.OPEN
    )
    created_at: Mapped[datetime] = Column(
        DateTime, default=lambda: datetime.now(timezone.utc), nullable=False
    )


class FixSession(Base):
    """闭环修复会话 — 记录每次修复尝试的完整上下文。

    用于：
    1. 修复前查询历史，避免重复尝试已失败的策略
    2. 积累修复经验，构建自进化修复知识库
    3. 审计修复过程，分析修复策略有效性
    """

    __tablename__ = "fix_sessions"

    id: Mapped[int] = Column(Integer, primary_key=True, autoincrement=True)
    project_id_fk: Mapped[int] = Column(
        Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[str] = Column(String(64), nullable=False, index=True)
    module_name: Mapped[str] = Column(String(128), nullable=False, index=True)
    error_type: Mapped[Optional[str]] = Column(String(64), nullable=True)
    error_message: Mapped[Optional[str]] = Column(Text, nullable=True)
    strategy_used: Mapped[str] = Column(
        String(64), nullable=False, default="unknown"
    )
    round_number: Mapped[int] = Column(Integer, nullable=False, default=1)
    success: Mapped[bool] = Column(Integer, nullable=False, default=0)
    fix_summary: Mapped[Optional[str]] = Column(Text, nullable=True)
    code_before: Mapped[Optional[str]] = Column(Text, nullable=True)
    code_after: Mapped[Optional[str]] = Column(Text, nullable=True)
    test_result: Mapped[Optional[str]] = Column(Text, nullable=True)
    loop_count: Mapped[int] = Column(Integer, nullable=False, default=0)
    resolved_at: Mapped[Optional[datetime]] = Column(DateTime, nullable=True)

    created_at: Mapped[datetime] = Column(
        DateTime, default=lambda: datetime.now(timezone.utc), nullable=False
    )
