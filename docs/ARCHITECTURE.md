# DevFlow CI — 系统架构

> 最后更新：2026-06-01 | 版本：v0.4.0

## 架构概览

```
┌──────────────────────────────────────────────────────────┐
│                     浏览器 (HTML/CSS/JS)                   │
│     轮询状态 + SSE 日志 + AI 流 + 对齐/方案确认交互         │
└──────────────────────┬───────────────────────────────────┘
                       │ HTTP / SSE
┌──────────────────────▼───────────────────────────────────┐
│                  FastAPI (main.py)                        │
│  REST API 20+ 端点 + 文件系统浏览 + 维护 API               │
│  后台 asyncio.Task 驱动工作流                              │
└──────────────────────┬───────────────────────────────────┘
                       │
┌──────────────────────▼───────────────────────────────────┐
│              WorkflowExecutor (executor.py)                │
│  对齐 → 上下文分析 → PM规划 → 并行模块 → 集成 → 审查 → 交付 │
│  永不卡死 + 异常自愈 + 流式 relay + 错误日志双写            │
└──────┬───────┬───────┬───────┬───────┬──────────────────┘
       │       │       │       │       │
  ┌────▼──┐ ┌─▼───┐ ┌─▼───┐ ┌─▼────┐ ┌─▼──────┐
  │Align  │ │Plan │ │Code │ │Revw  │ │Integr   │
  │+Biz   │ │Agent│ │+Test│ │+Repair│ │+Global  │
  └───────┘ └─────┘ └─────┘ └──────┘ └─────────┘
       │       │       │       │       │
       └───────┴───────┴───┬───┴───────┘
                           │
              ┌────────────▼────────────┐
              │    SQLite (devflow.db)   │
              │  projects, module_tasks  │
              │  project_logs, error_logs│
              │  fix_sessions            │
              └─────────────────────────┘
```

## 数据流

```
用户需求 + 可选目录
  → POST /api/projects
  → execute_alignment() → aligned → [用户 confirm_plan]
  → context_analysis → planning → plan_ready → [可选 confirm_plan]
  → 并行模块执行 → 集成 → 全局审查 → 打包 vN/ → completed
```

## 状态机

```
created → aligning → aligned → planning → plan_ready
  → executing → integrating → reviewing → completed
                                           → failed / needs_review / cancelled
```

## Agent 清单

| Agent | 文件 | 职责 |
|-------|------|------|
| AlignmentAgent | `agents/alignment_agent.py` | 需求对齐，基于文档生成执行计划 |
| BusinessPlannerAgent | `agents/business_planner.py` | 商业文档模式规划 |
| PlannerAgent | `agents/planner.py` | 任务拆解、依赖推断、多方案对比 |
| ModuleAgents | `agents/module_agents.py` | 分析→编码→测试 |
| ReviewerAgent | `agents/reviewer.py` | 代码审查 |
| RepairAgent | `agents/repair_agent.py` | 反思修复、历史案例 |
| IntegratorAgent | `agents/integrator.py` | 组装项目、注入 autopilot |
| GlobalReviewerAgent | `agents/integrator.py` | 全局审查、评分 |

## 工作流模块

| 模块 | 路径 | 说明 |
|------|------|------|
| 执行器 | `workflow/executor.py` | 阶段编排、SSE、占位生成 |
| 图定义 | `workflow/langgraph_def.py` | LangGraph 节点拓扑 |
| 自愈 | `workflow/auto_fix.py` | 错误分类、策略调度 |
| 闭环修复 | `workflow/closed_loop.py` | 模块级修复循环 |
| 测试运行 | `workflow/test_runner.py` | pytest 沙箱执行 |
| 流式 relay | `workflow/stream_relay.py` | DeepSeek stream → SSE |
| 错误日志 | `workflow/error_logger.py` | ErrorLog 表读写 |

## 数据库表

| 表 | 说明 |
|----|------|
| projects | 项目主记录、对齐 JSON、状态 |
| module_tasks | 模块任务与代码 |
| project_logs | 日志条目 |
| error_logs | 结构化错误（决策 #017） |
| fix_sessions | 修复会话记录 |

## 技术栈

| 层次 | 技术 | 版本 |
|------|------|------|
| 语言 | Python | 3.11+ |
| Web | FastAPI + uvicorn | 0.115 / 0.34 |
| LLM | DeepSeek (langchain-openai) | 0.2.14 |
| 工作流 | LangGraph | 0.2.56 |
| 数据库 | SQLAlchemy 2.0 + aiosqlite | — |
| 前端 | Vanilla HTML/CSS/JS | — |

## 关键设计决策

| # | 决策 | 位置 |
|---|------|------|
| #006 | 永不卡死 (blocked > failed) | docs/decisions.md |
| #015 | 需求对齐强制前置 | docs/decisions.md |
| #016 | 商业文档路由 + 关键词库 | docs/decisions.md |
| #017 | 流式 AI 输出 + ErrorLog | docs/decisions.md |
| #018 | docs/ 为记忆文件权威源 | docs/decisions.md |

## 协作与文档

| 文件 | 用途 |
|------|------|
| docs/MEMORY_INDEX.md | 记忆文件索引 |
| docs/API.md | REST API 参考 |
| docs/DEVELOPMENT.md | 本地开发指南 |
| scripts/session_start.* | 会话启动 |
