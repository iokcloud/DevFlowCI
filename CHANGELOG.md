# Changelog

本文件记录 DevFlow CI 的重要变更。格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)。

## [Unreleased]

### 修复
- **集成/全量测试沙箱**：写入 `main.py`、合并 `requirements` 并 pip 安装到 `.deps`；补全目录上已通过依赖模块；collect error 不再误触发「0 failed 再修」

### 新增
- **编码两轮生成**（`CODE_GEN_TWO_PHASE=true`）：先 `code` 后 `test_code`，各阶段独立就绪校验，降低 JSON 截断（`agents/module_agents.py`）
- **已通过模块 API 注入**：按 `dependencies` / 项目记忆 / `module_results` 与目录 `{name}.py` 提取公共签名，注入 analyze 与 code（`workflow/passed_module_context.py`）

### 修复
- **增量规划**：已通过模块可作为 `external_modules` 通过依赖校验；无效依赖自动修剪（`8157976`）
- **CI**：对齐 ruff 0.11.3 项 lint；pre-commit 增加 ruff；Actions 升级 checkout/setup-python v6（`7469f66`）

## [0.4.10] - 2026-06-03

### 修复
- **规划验证**：修复 LLM 生成模块依赖不存在的模块名导致反复验证失败的 bug（`planner.py` + `executor.py`）
- **备选方案静默丢弃**：plan_b 验证失败时现在记录 warning 日志而非无声丢弃
- **对齐重试**：对齐阶段失败重试时注入上轮验证错误反馈，避免盲目重试
- **数据库会话泄漏**：`sse_bridge.py` 中 `create_task` 改为 `await`，消除 AsyncSession 未关闭警告
- **迁移协程冲突**：`db.py` 中 `command.upgrade` 用 `asyncio.to_thread` 隔离，消除嵌套事件循环警告
- **API 输入校验**：`CreateProjectRequest` 增加 `max_length`；`get_history` limit 钳制到 100

### 新增
- **BusinessPlannerAgent 验证**：`validate()` 方法校验 7 个维度（顶层字段、market_analysis、business_model、roadmap、risks）
- **AlignmentAgent 依赖交叉验证**：`validate_alignment()` 检查 dependencies 引用的模块是否存在
- **extract_json 分级日志**：截断补全策略记录 warning，全部失败记录 error
- **ModuleAgents 错误上下文**：JSON 解析失败时附加 Agent 名 + 模块名到异常消息
- **cancel 终态守卫**：已终态项目调用 cancel 返回 400

### 测试
- 新增 61 个单元测试：`test_planner_validate.py` (21)、`test_alignment_validate.py` (17)、`test_business_planner_validate.py` (23)
- 全部 257 个测试通过

### 文档
- `docs/AGENT_PROMPT_TEMPLATES.md`：新增 BusinessPlannerAgent 章节 + 跨 Agent 可靠性机制

## [0.4.9] - 2026-06-01

### 新增
- 历史项目：单条删除 + 批量清理（含停滞项目）
- 目录扫描摘要 Web 展示；扩展 `.json`/`.yaml`/`.csv` 资料扫描
- 选目录「强制新建」；`/api/health` 返回 `api_version`

### 变更
- 全 Pro 对齐：autopilot 交付模板、文档摘要、批量错误分析统一 `create_llm_*`
- GitHub CI（nightly / multi / pytest）显式 `DEEPSEEK_MODEL=deepseek-v4-pro`

[0.4.9]: https://github.com/iokcloud/DevFlowCI/releases/tag/v0.4.9

## [0.4.8] - 2026-06-01

### 新增
- DeepSeek V4 分 Agent 配置：`create_llm_reasoning` / `create_llm_json` / `create_llm_text`（thinking + json_object）
- SSE 推送 `project_snapshot`，Web 状态轮询降为 10s 兜底

### 验证
- v4-pro multi（分 Agent，3 次连续本地）：`proj-fa53e28408da` ~3m29s、`proj-f382a95e50ea` ~2m32s、`proj-6ce35adfbf8b` ~2m16s，均 2 passed / 0 blocked
- v4-flash multi 本地：`proj-e976cf570f96`，~2m05s，2 passed / 0 blocked
- 对比：未分 Agent 的 Pro multi 曾 0/2 blocked（~32min）；分 Agent 后 Pro 稳定可用

### 变更
- 默认模型改回 **deepseek-v4-pro**（全 Pro + 分 Agent）
- E2E workflow 超时上调以适配 Pro 推理耗时
- Weekly Business E2E 增加 `business-multi` job（cap=2，与 full 并行）
- `business-multi-e2e.yml` 失败时 Slack 通知
- `.env.example` / `docs/DEVELOPMENT.md` 补充 `BUSINESS_MVP_MAX_MODULES` 生产建议

[0.4.8]: https://github.com/iokcloud/DevFlowCI/releases/tag/v0.4.8

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
[0.4.10]: https://github.com/iokcloud/DevFlowCI/releases/tag/v0.4.10
[0.2.0]: https://github.com/iokcloud/DevFlowCI/releases/tag/v0.2.0
