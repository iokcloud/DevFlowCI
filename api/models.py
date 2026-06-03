"""共享请求模型 — 被 main.py 和 api/ 模块共同引用。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from database.models import ProjectStatus

_TERMINAL_PROJECT_STATUSES = {
    ProjectStatus.COMPLETED,
    ProjectStatus.FAILED,
    ProjectStatus.NEEDS_REVIEW,
    ProjectStatus.CANCELLED,
    ProjectStatus.FINALIZED,
}

_ACTIVE_PROJECT_STATUSES = {
    ProjectStatus.CREATED,
    ProjectStatus.ALIGNING,
    ProjectStatus.ALIGNED,
    ProjectStatus.PLANNING,
    ProjectStatus.PLAN_READY,
    ProjectStatus.EXECUTING,
    ProjectStatus.INTEGRATING,
    ProjectStatus.REVIEWING,
}

class CreateProjectRequest(BaseModel):
    """创建项目请求体。"""
    requirement: str = Field(default="", max_length=5000, description="用户自然语言需求")
    directory: str | None = Field(default=None, max_length=500, description="项目目录绝对路径（选填）")
    mode: str = Field(default="auto", description="计划类型: auto/technical/business")
    force_new: bool = Field(default=False, description="强制创建新项目，跳过去重（重试场景使用）")


class UpdateDisplayNameRequest(BaseModel):
    """更新历史项目显示名称。"""
    display_name: str = Field(..., min_length=1, max_length=128, description="自定义标题")


class IterateProjectRequest(BaseModel):
    """开始下一轮迭代改进。"""
    addendum: str = Field(
        default="",
        description="本轮需求补充说明（可选）",
    )
    module_names: list[str] = Field(
        default_factory=list,
        description="指定补跑模块；为空则默认全部 blocked 模块",
    )


class RequirementAddendumRequest(BaseModel):
    """仅追加需求补充（不启动迭代）。"""
    text: str = Field(..., min_length=1, description="补充说明")


class ConfirmPlanRequest(BaseModel):
    """确认规划请求体。"""
    modules: list[dict[str, Any]] | None = Field(
        None, description="可选的修改后模块列表，为空则使用原始规划"
    )
    plan_choice: str | None = Field(
        None, description="方案选择: 'A' 或 'B'（多方案生成时有效）"
    )
    dependencies_override: list[dict[str, Any]] | None = Field(
        None, description="用户修改后的依赖清单"
    )


class FeedbackRequest(BaseModel):
    """人工反馈请求体。"""
    original_code: str = Field(default="", description="系统生成的代码片段")
    modified_code: str = Field(default="", description="人工修改后的代码片段")
    file: str = Field(default="", description="修改的文件路径")
    line_range: str = Field(default="", description="修改的行号范围")
    fix_type: str = Field(default="Bug修复", description="修改类型")
    description: str = Field(default="", description="修改说明")


class RollbackRequest(BaseModel):
    """回滚请求体。"""
    version: str = Field(default="", description="要回滚到的版本号，如 'v1'")


class CleanupProjectsRequest(BaseModel):
    """批量清理历史项目。"""
    statuses: list[str] = Field(
        default=["completed", "cancelled", "failed", "needs_review"],
        description="要删除的项目状态列表",
    )
    include_stale: bool = Field(
        default=True,
        description="是否包含停滞的非终态项目（planning/created/aligning 等）",
    )
    stale_minutes: int = Field(default=10, ge=1, le=1440)
    delete_deliveries: bool = Field(
        default=True,
        description="是否同时删除 deliveries 目录下的交付物（默认开启）",
    )


class DeliveryCleanupRequest(BaseModel):
    """按建议路径删除 deliveries 下的冗余交付物。"""
    paths: list[str] = Field(default_factory=list, description="要删除的相对路径列表")
    dry_run: bool = Field(default=False, description="仅预览，不实际删除")

