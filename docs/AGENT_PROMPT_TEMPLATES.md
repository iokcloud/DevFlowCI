# AI Agent Prompt 模板库

> 本文档是 DevFlow CI 的 Prompt 权威来源（决策 #018）。
> 格式遵循 AI_COLLABORATION_GUIDE.md §2.2.5。
>
> **结构**：
> - **§A Agent 角色 Prompt** — 系统内 8 个 Agent 的实际实现说明（v0.4）
> - **§B 人类任务 Prompt 模板** — 人类在新会话中分派任务时可复制的模板

---

# §A Agent 角色 Prompt（v0.4 实现）

## Agent 角色总览

```
┌──────────────────┐
│ AlignmentAgent   │ ← 需求对齐（前置强制），基于文档内容生成计划
└────────┬─────────┘
         │ aligned → 用户确认
┌────────▼─────────┐
│  PlannerAgent    │ ← PM 规划，任务拆解 + 依赖推断 + 多方案对比
└────────┬─────────┘
         │ 模块列表
┌────────▼─────────┐
│  ModuleAgents    │ ← 分析 → 编码 → 测试 → 审查 循环（最多5次正常重试）
│  (Analyze/Code/  │
│   Test 三合一)    │
└────────┬─────────┘
         │ 审查不通过 → RepairAgent 自愈（退避/修改/反思/历史案例 → 最多8轮）
┌────────▼─────────┐
│ IntegratorAgent  │ ← 组装项目 + 生成 README
└────────┬─────────┘
         │
┌────────▼─────────┐
│GlobalReviewerAgent│ ← 全局审查 + 评分
└────────┬─────────┘
         │
    ┌────▼────┐
    │ 打包交付 │
    └─────────┘
```

## 0. AlignmentAgent（需求对齐分析师）— 前置强制

**文件**：`agents/alignment_agent.py`
**职责**：在所有构建工作流启动前强制执行。基于项目文档实际内容生成结构化对齐分析（摘要、假设、风险、建议模块、待澄清问题）。

**核心 Prompt**（`ALIGNMENT_SYSTEM_PROMPT`）：

```
你是一位严格的需求分析师。你的任务是基于**项目文档实际内容**生成执行计划建议。

## 核心约束
1. **文档优先**：只能基于提供的文档摘要生成计划，不得凭空添加。
2. **禁止通用建议**：不得输出"改善代码质量""增强日志"等通用建议。
3. **信息不足即声明**：文档不足以生成计划 → 返回 insufficient_info。
4. **关联溯源**：每个模块的 reason 必须引用文档文件名或段落。
```

**自检机制**：`_self_check_plan()` 用关键词匹配验证每个模块是否能在文档摘要中找到依据，找不到的标记 `flagged: true`。

**验证规则**（`validate_alignment()`）：
- 字段完整性：summary / assumptions / risks / plan / questions 必须存在
- plan 数组内每个元素必须有 module / description / reason
- 若 module 包含 dependencies 字段，引用的每个模块名必须在 plan 中存在
- dependencies 为非 list 类型时跳过校验（不 crash）

---

## 1. PlannerAgent（PM 规划）

**文件**：`agents/planner.py`
**职责**：将需求拆解为可独立开发的模块 DAG。支持多方案对比（方案A/B）、依赖推断、人类偏好注入。

**核心 Prompt**（`PLANNER_SYSTEM_PROMPT`）：

```
你是一位资深软件架构师 / PM。将用户需求拆解为可独立开发的模块任务列表。

输出格式：
{
  "modules": [
    {"module_name": "auth", "description": "...", "dependencies": [], "type": "backend"}
  ],
  "global_requirements": ["使用 SQLite", "所有 API 返回 JSON"]
}

规则：
- 自动判断粒度（简单1-2模块，中等2-4，复杂4-10）
- 依赖目标必须存在：dependencies 中引用的每个模块名必须已在 modules 中定义
- 无循环依赖
- 增量开发时优先复用现有模块
```

**多方案**（`ALTERNATIVE_PLAN_PROMPT`）：生成方案A/B对比，含结构化优劣势分析。

**验证规则**（`validate_plan()`）：
- 结构校验：modules 必须是非空数组，每个模块必须有 module_name / description / type
- 依赖校验：dependencies 中引用的模块名必须在 modules 列表中存在；dependencies 必须是数组
- 循环依赖检测：DFS 检测直接/间接循环
- 备选方案 plan_b 验证失败时记录 warning 日志并丢弃（不影响主方案）

---

## 1b. BusinessPlannerAgent（商业计划分析师）

**文件**：`agents/business_planner.py`
**职责**：当文档类型被识别为 business 时激活。基于市场调研、行业分析等商业文档生成结构化商业项目计划建议书。

**核心 Prompt**（`BUSINESS_PLANNER_PROMPT`）：

```
你是一位资深商业分析师与战略顾问。基于提供的市场调研文档生成商业项目计划建议书。

输出格式：executive_summary / market_analysis / product_positioning / 
          business_model / roadmap / risks_and_mitigations / recommendations
```

**验证规则**（`validate()`）：
- 顶层字段：executive_summary / product_positioning / recommendations 非空字符串
- market_analysis：必须是 dict，target_audience / competition / trends 均非空
- business_model：必须是 dict，revenue_streams 是数组，cost_structure 非空
- roadmap：至少 2 个阶段，每阶段含 phase / duration + 非空 actions / milestones
- risks_and_mitigations：severity 必须为 high / medium / low

---

## 2. ModuleAgents（分析/编码/测试 三合一）

**文件**：`agents/module_agents.py`
**职责**：对每个模块执行 分析→编码→测试 循环。

**流程**：`analyze(模块名, 描述, 上下文) → code(模块名, 规格, 反馈) → test(模块名, 代码, 规格)`

**错误处理**：
- `analyze()` 和 `test()` 中 JSON 解析失败时，异常附加模块名上下文（如 `分析师[auth] JSON 解析失败`）
- `code()` 内置 3 次重试：每次 JSON 解析失败后将错误信息注入 prompt 重试
- 输出使用 `.get()` 带默认值兜底，避免 LLM 缺少字段时 crash

---

## 3. ReviewerAgent（代码审查）

**文件**：`agents/reviewer.py`
**职责**：审查模块代码，返回通过/不通过及问题清单。

**自检清单**：
- 🔴 阻断级：可编译、类型正确、无密钥泄露、无调试代码
- 🟡 重要级：Docstring完整、输入校验、异常处理
- 🟢 建议级：风格一致、长度合理

---

## 4. RepairAgent（异常自愈修复）

**文件**：`agents/repair_agent.py`
**职责**：当模块审查不通过时，用反思+历史案例进行修复。

**修复策略**（优先级递减）：
1. `direct_retry` — 指数退避后重试
2. `modify_code` — 将审查反馈注入编码Agent修改代码
3. `repair_agent` — RepairAgent 反思修复
4. `history_case` — 检索历史相似案例参考修复
5. `block_with_stub` — 所有策略耗尽 → 生成占位文件阻塞

---

## 5. IntegratorAgent（项目集成）

**文件**：`agents/integrator.py`
**职责**：接收所有模块代码，组装为完整项目。感知 blocked 模块并生成占位文件。

---

## 6. GlobalReviewerAgent（全局审查）

**文件**：`agents/integrator.py`（与 IntegratorAgent 同文件）
**职责**：端到端审查最终交付物。输出评分（0-100）、通过/不通过、问题清单。

---

## 通用自检清单（提交前必过）

- [ ] `python -m py_compile` 所有 .py 文件通过
- [ ] 无 `print()` / `console.log()` 遗留
- [ ] 无硬编码密钥/Token
- [ ] 公共函数有 Docstring 和类型注解
- [ ] 外部调用有 try/except

---

## 跨 Agent 可靠性机制

### JSON 解析（`extract_json()`）
- **策略链**：直接解析 → ```json 代码块 → 截断补全 → 花括号匹配 → 抛出 ValueError
- **日志分级**：策略2b/3b（截断补全）记录 `warning`；全部失败记录 `error`
- **调用处防护**：各 Agent 在 `extract_json()` 失败时附加 Agent 名 + 模块名到异常消息

### 重试反馈注入
- **规划重试**（`executor.py:plan_only`）：验证失败时将错误列表注入 `planning_context`
- **对齐重试**（`executor.py:execute_alignment`）：验证失败时将错误列表注入 `retry_req`

### 依赖交叉验证
- **PlannerAgent**：`dependencies` 中的模块名必须在 `modules` 列表中，或属于 `project_memory` 中 **passed** 的 `external_modules`；DFS 循环检测
- **AlignmentAgent**：若 `module.dependencies` 存在，引用的每个模块名必须在 `plan` 中存在

### CI 与本地 lint 对齐
- CI 使用 `requirements-dev.txt` 中的 **ruff 版本**（当前 `0.11.0`），勿仅用全局新版 ruff 判断「可提交」
- pre-commit 已含 `ruff check .`；提交前也可用 `venv\Scripts\python.exe -m ruff check .`

### 数据库会话管理
- 迁移使用 `asyncio.to_thread()` 隔离避免嵌套事件循环冲突
- 状态同步使用 `await` 代替 `create_task` 防止会话泄漏

---

# §B 人类任务 Prompt 模板

> 人类在开始新会话时，可根据任务类型从以下模板中选取，粘贴到对话开头。

### 1. 新功能开发

**适用场景**：从零实现一个新的功能模块。

```
## 任务：开发 [模块名称]

### 背景
[为什么需要这个功能？]

### 功能描述
[具体要实现什么？输入/输出是什么？]

### 技术约束
- 使用 Python 3.11+
- 遵循 docs/CODING_STANDARDS.md
- 所有公共函数必须有类型注解和 Docstring

### 质量要求
- mypy strict 模式通过
- 异常处理覆盖所有外部调用
- 无 print() 或调试代码遗留

### 期望输出
- [ ] 核心实现文件
- [ ] 单元测试
- [ ] 如有架构影响，更新 docs/ARCHITECTURE.md
- [ ] 如有新决策，更新 docs/decisions.md
```

### 2. Bug 修复

```
## 任务：修复 Bug #[编号]

### 现象
[描述 Bug 的表现]

### 期望行为
[正确行为应该是什么？]

### 约束
- 先写会失败的测试用例来复现 Bug
- 修复后确保该测试通过
- 补充教训到 docs/LEARNINGS.md
```

### 3. 代码重构

```
## 任务：重构 [模块/文件]

### 约束
- ⚠️ 不改变外部行为
- 重构前确保已有测试覆盖现有行为
- 如涉及设计模式变更，先写 docs/decisions.md
```

### 4. 代码审查准备

```
## 任务：为以下变更做 PR 前审查

### 请按 AI_COLLABORATION_GUIDE.md §4.1 的三级清单自检
- 🔴 阻断级：编译、类型、密钥泄露、调试代码
- 🟡 重要级：Docstring、输入校验、依赖文件
- 🟢 建议级：格式化、函数长度、测试覆盖率
```

### 5. 依赖更新与安全审计

```
## 任务：依赖安全审计与更新

1. pip list --outdated / pip-audit
2. 逐项更新，每次只升级一个包
3. 更新后运行完整测试套件
```

---

## 模板维护日志

| 日期 | 变更 | 原因 |
|------|------|------|
| 2026-05-31 | 初始骨架：7 个 Agent + 5 个人类任务模板 | 项目初始化 |
| 2026-05-31 | §A 重写：8 个 Agent 实际实现 | 规范合规修复 |
| 2026-06-01 | 合并 §A + §B 为 docs/ 权威源 | 决策 #018 |
| 2026-06-03 | 新增 BusinessPlannerAgent 验证规则 + 跨 Agent 可靠性机制 + 依赖交叉验证规范 | 防御加固 v2 |
