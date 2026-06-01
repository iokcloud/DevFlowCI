# Changelog

本文件记录 DevFlow CI 的重要变更。格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)。

## [0.4.0] - 2026-06-01

### 新增
- 需求对齐阶段（AlignmentAgent，强制前置）
- 流式 AI 输出 + ErrorLog 维护 API（决策 #017）
- 商业文档模式路由（决策 #016）
- 协作记忆体系：`docs/` 权威源、MEMORY_INDEX、会话脚本
- `docs/API.md`、`docs/DEVELOPMENT.md`、`LICENSE`
- `scripts/test_flow.bat`、`scripts/session_start/end` 系列脚本
- test_flow.py 心跳与自动 confirm（v0.4 对齐流程）

### 变更
- README / ARCHITECTURE / 版本号统一为 v0.4.0
- test_e2e.py 适配 v0.4（移除 `type` 字段，增加对齐确认）
- `.env.example` 与 `config.py` 默认值对齐
- pytest 加入 `requirements.txt`

### 修复
- 工作流异常后状态不回写（`_run_workflow_after_alignment` / `_run_execution`）
- 模块进度执行期不可见（增量 DB 持久化）
- integrator 流式输出对非字符串 `project_structure` 切片报错
- test_flow Windows UTF-8 编码崩溃

### 工程
- Git 仓库初始化 + baseline commit
- E2E 冒烟验证通过（test_flow ~289s → completed）

## [Unreleased]

### 新增
- 核心模块单元测试（`tests/`，32 项：auto_fix / case_store / models）
- GitHub Actions CI（语法检查 + pytest）
- `pytest-asyncio` 依赖
- `tests/test_error_logger.py`、`tests/test_executor.py`（路由、日志队列、DB 同步）
- `scripts/stop_server.bat`、`scripts/install_hooks.bat`、`scripts/pre_commit_check.bat`
- `tests/test_executor_integration.py`（5 项 mock LLM 集成测试）
- `.github/pull_request_template.md`

### 变更
- `docs/DEVELOPMENT.md`：单元测试说明、Windows 端口占用 FAQ、pre-commit 安装
- `README.md`：CI 状态徽章
- `CHANGELOG.md`：仓库链接改为 iokcloud/DevFlowCI

### 修复
- `get_error_stats`：`func.case` → `sqlalchemy.case`

## [0.2.0] - 2026-05-31

### 新增
- 多 Agent 工作流、异常自愈、版本化交付、Autopilot 模板
- AI 协作规范（AI_COLLABORATION_GUIDE.md）

[0.4.0]: https://github.com/iokcloud/DevFlowCI/compare/v0.2.0...v0.4.0
[0.2.0]: https://github.com/iokcloud/DevFlowCI/releases/tag/v0.2.0
