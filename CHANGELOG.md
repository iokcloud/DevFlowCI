# Changelog

本文件记录 DevFlow CI 的重要变更。格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)。

## [Unreleased]

## [0.4.1] - 2026-06-01

### 新增
- `scripts/memory_search.py` / `memory_search.bat` — 记忆检索 CLI（6 项单元测试）
- `.github/workflows/nightly-e2e.yml` — 定时 E2E 冒烟（需 `DEEPSEEK_API_KEY` secret）
- `test_flow_business.py` / `scripts/test_flow_business.bat` — 商业文档模式 E2E 冒烟
- `.github/workflows/weekly-business-e2e.yml` — 每周商业 E2E
- 集成测试：商业对齐、多模块 blocked、GlobalReviewer dict 回归

### 变更
- Nightly E2E：`concurrency`、手动 `scenario` 选择（smoke/business）
- CI / E2E workflows：`FORCE_JAVASCRIPT_ACTIONS_TO_NODE24` 消除 Node 20 弃用警告
- `docs/MEMORY_INDEX.md`、`docs/DEVELOPMENT.md`、`AI_COLLABORATION_GUIDE.md` 同步

### 修复
- GlobalReviewer 对 dict `project_structure` 切片报错（nightly E2E 验证通过）

## [0.4.0] - 2026-06-01

### 新增
- 需求对齐阶段（AlignmentAgent，强制前置）
- 流式 AI 输出 + ErrorLog 维护 API（决策 #017）
- 商业文档模式路由（决策 #016）
- 协作记忆体系：`docs/` 权威源、MEMORY_INDEX、会话脚本
- `docs/API.md`、`docs/DEVELOPMENT.md`、`LICENSE`
- `scripts/test_flow.bat`、`scripts/session_start/end` 系列脚本
- test_flow.py 心跳与自动 confirm（v0.4 对齐流程）
- 核心模块单元测试（50 项）+ WorkflowExecutor 集成测试（5 项，mock LLM）
- GitHub Actions CI（语法检查 + pytest 分层：PR 快测 / master 全量）
- `pytest-asyncio`、`scripts/stop_server.bat`、`scripts/install_hooks.bat`
- `.github/pull_request_template.md`、`scripts/setup_branch_protection.ps1`

### 变更
- README / ARCHITECTURE / 版本号统一为 v0.4.0
- test_e2e.py 适配 v0.4（移除 `type` 字段，增加对齐确认）
- `.env.example` 与 `config.py` 默认值对齐
- README CI 状态徽章；`master` 分支保护（CI + PR）

### 修复
- 工作流异常后状态不回写（`_run_workflow_after_alignment` / `_run_execution`）
- 模块进度执行期不可见（增量 DB 持久化）
- integrator 流式输出对非字符串 `project_structure` 切片报错
- test_flow Windows UTF-8 编码崩溃
- `get_error_stats`：`func.case` → `sqlalchemy.case`

### 工程
- Git 仓库初始化 + GitHub 远程 `iokcloud/DevFlowCI`
- E2E 冒烟验证通过（test_flow ~289s → completed）
- 55 项 pytest 全部通过

## [0.2.0] - 2026-05-31

### 新增
- 多 Agent 工作流、异常自愈、版本化交付、Autopilot 模板
- AI 协作规范（AI_COLLABORATION_GUIDE.md）

[0.4.1]: https://github.com/iokcloud/DevFlowCI/releases/tag/v0.4.1
[0.4.0]: https://github.com/iokcloud/DevFlowCI/releases/tag/v0.4.0
[0.2.0]: https://github.com/iokcloud/DevFlowCI/releases/tag/v0.2.0
