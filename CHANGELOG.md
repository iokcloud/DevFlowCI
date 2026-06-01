# Changelog

本文件记录 DevFlow CI 的重要变更。格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)。

## [Unreleased]

### 变更
- Weekly Business E2E 增加 `business-multi` job（cap=2，与 full 并行）
- `business-multi-e2e.yml` 失败时 Slack 通知
- `.env.example` / `docs/DEVELOPMENT.md` 补充 `BUSINESS_MVP_MAX_MODULES` 生产建议

## [0.4.7] - 2026-06-01

### 新增
- `BUSINESS_MVP_MAX_MODULES>1` 多模块商业路径：从 Phase-1 生成多模块技术需求
- `_sanitize_business_multi_modules`：cap>1 时强制 backend 轻量模块，避免 PM 产出前端/ML 导致全 blocked
- `test_flow_business.py` 支持 `TEST_FLOW_BUSINESS_MODE=multi`
- `.github/workflows/business-multi-e2e.yml`（手动触发，cap=2）

### 变更
- 商业多模块：pytest 全绿可放行（与单模块 MVP 门控一致）
- `compact_mvp` 贯穿分析 / 编码 / 审查阶段
- 多模块 E2E 验证上限设为 2 模块（cap=3 仍不稳定，不推荐生产默认）

### 验证
- Business Multi-Module E2E 通过（run 26761439750，2 模块 2 passed，约 2m31s）

[0.4.7]: https://github.com/iokcloud/DevFlowCI/releases/tag/v0.4.7

## [0.4.6] - 2026-06-01

### 新增
- Web：SSE 模块状态联动 module-stats-bar、blocked failure_reason 详情
- 商业确认前技术 MVP 预览 API + UI
- `scripts/analyze_blocked.bat`（Windows 一键排查）

### 变更
- 纯商业默认 Phase-1 单模块（`BUSINESS_MVP_MAX_MODULES` 默认 1）
- 商业 `mvp_max_modules` 写入 alignment 并在 plan_ready 执行阶段传递
- MVP 单模块：pytest 全绿可放行（审查 FAIL 时降级）
- PM cap=1 强制单模块 + 描述对齐技术需求

### 验证
- Weekly full 商业 E2E 通过（run 26759523088，约 2m，无 is_prime）

[0.4.6]: https://github.com/iokcloud/DevFlowCI/releases/tag/v0.4.6

## [0.4.5] - 2026-06-01

### 修复
- `extract_json` 支持截断/未闭合 JSON 补全，减少 coder 输出被截断即 blocked
- 编码者使用 2× max_tokens + 最多 3 次 JSON 解析重试
- MVP 模式下分析师/编码者附加单文件行数上限；Phase-1 技术需求禁止 SQLite/CLI

[0.4.5]: https://github.com/iokcloud/DevFlowCI/releases/tag/v0.4.5

## [0.4.4] - 2026-06-01

### 新增
- Reviewer MVP 模式：单模块交付时放宽审查标准
- Weekly 商业 E2E 使用 `TEST_FLOW_BUSINESS_MODE=full`（纯商业 Phase-1，无 is_prime）

### 变更
- 商业确认后默认取 roadmap Phase-1 首项作为单模块技术 MVP（`cap=1`）
- `test_flow_business.py` 支持 `TEST_FLOW_BUSINESS_MODE=smoke|full`

[0.4.4]: https://github.com/iokcloud/DevFlowCI/releases/tag/v0.4.4

## [0.4.3] - 2026-06-01

### 新增
- `scripts/analyze_blocked.py` — 分析 blocked 模块失败原因
- `.github/workflows/learnings-cluster-weekly.yml` — 每周 LEARNINGS 聚类
- `tests/test_business_routing.py` — is_prime 商业 MVP 短路径

### 修复
- 商业确认含 `is_prime` 时强制单模块技术需求（对齐 test_flow 冒烟）

[0.4.3]: https://github.com/iokcloud/DevFlowCI/releases/tag/v0.4.3

## [0.4.2] - 2026-06-01

### 新增
- `test_flow_quality.py` — E2E 模块质量断言（`TEST_FLOW_MIN_PASSED`）
- `scripts/learnings_cluster.py` — LEARNINGS 按类别聚类摘要
- Web UI：模块统计条、最近 ERROR/WARN 面板、商业计划原始 JSON
- Nightly 失败可选 Slack 通知（`SLACK_WEBHOOK_URL` secret）
- 集成测试：`insufficient_info` 商业兜底、多方案 `alternative_plan`

### 变更
- 商业确认后 MVP 模块裁剪（`BUSINESS_MVP_MAX_MODULES=3`）
- `test_flow_business.py` 读取 `alignment` 字段；超时 1800s
- 版本号 v0.4.2

### 修复
- PR #6/#7：商业 E2E 字段检测与超时

[0.4.2]: https://github.com/iokcloud/DevFlowCI/releases/tag/v0.4.2

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
