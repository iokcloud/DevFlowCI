# DevFlow CI

[![CI](https://github.com/iokcloud/DevFlowCI/actions/workflows/ci.yml/badge.svg)](https://github.com/iokcloud/DevFlowCI/actions/workflows/ci.yml)

> 项目型开发伙伴 — 自动化多 Agent 编码工作流系统 **v0.4.7**

用户输入自然语言需求，系统自动规划、拆解、分派给不同 AI Agent 并行/串行执行，最终产出完整的项目源代码、测试和部署文件，并通过审查循环保证质量。支持在已有项目目录上进行增量开发，并提供项目级记忆。

## 文档导航

| 读者 | 从这里开始 |
|------|-----------|
| 新用户 / 快速上手 | 本文 → [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) |
| API 集成 | [docs/API.md](docs/API.md) |
| AI 协作 | [AI_COLLABORATION_GUIDE.md](AI_COLLABORATION_GUIDE.md) → [docs/MEMORY_INDEX.md](docs/MEMORY_INDEX.md) |
| 架构 | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| 编码规范 | [docs/CODING_STANDARDS.md](docs/CODING_STANDARDS.md) |
| 变更记录 | [CHANGELOG.md](CHANGELOG.md) |

## 架构

```
用户输入 + 可选项目目录 → 需求对齐（强制）→ 上下文分析 → PM Agent（规划）→ 并行模块 Agent → 集成 Agent → 全局审查 → 打包交付
                                    ├─ 分析师                          ├─ TODO.md（未完成模块清单）
                                    ├─ 编码者  ←── 审查重试循环（最多5次，超限→blocked）
                                    ├─ 测试者  │
                                    └─ 审查者 ─┘
```

- 8 个 AI Agent：需求对齐分析师、PM、分析、编码、测试、审查、集成、全局审查
- **异常自愈**：RepairAgent 反思修复 + 历史案例库进化
- **流式 AI 输出**：DeepSeek stream → SSE 实时展示
- LangGraph 工作流编排，支持 DAG 并行执行
- DeepSeek LLM（通过 langchain-openai）
- FastAPI + SSE 实时日志
- SQLite 任务状态持久化 + JSON 成功案例记忆
- **项目级记忆**：对同一目录的多次操作保留历史上下文

## 快速启动

详见 [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)。

```bash
python -m venv venv
source venv/Scripts/activate   # Windows Git Bash
pip install -r requirements.txt
cp .env.example .env           # 填入 DEEPSEEK_API_KEY
python main.py
```

浏览器访问：`http://localhost:8000`

## 使用方式

### 场景 1：从零开始的新项目
在"需求描述"框中输入需求，留空"项目目录"，点击"开始构建"。

### 场景 2：分析已有项目
在"项目目录"框中输入本地绝对路径（如 `D:/projects/my-app`），留空需求描述，系统将自动分析代码并找出可优化之处。

### 场景 3：在已有项目上增量开发
同时填写需求描述和项目目录，系统将结合现有代码和你的需求进行增量开发。

### 需求对齐（强制阶段）
所有构建工作流的第一步，在进入规划之前强制执行：
- **AlignmentAgent**（需求分析师）融合用户需求和项目文档，识别技术假设、潜在风险
- 输出结构化执行计划建议（模块列表、假设、风险、待澄清问题）
- 前端展示"需求分析中"过渡动画，完成后以模块卡片列表展示执行计划
- **用户确认**：每项可勾选是否包含、可编辑描述，确认后进入规划阶段
- 超时（300s）系统保持 aligned 状态但不会自动执行，防止错误开工
- 对齐产生的假设和风险自动记录到 `docs/decisions.md`，确认后的计划写入 `DELIVERY_PLAN.md`

### 永不卡死策略
- 模块审查重试 5 次，超限后标记为 **blocked**（而非失败）
- blocked 模块生成骨架占位文件，附带 TODO.md
- 项目完成后可下载包含占位文件的完整 ZIP 包

### 异常自愈（Auto-Healing）
系统在模块失败时会自动尝试修复，减少人工干预：
- **错误分类**：自动识别 API 超时、语法错误、测试失败、审查不通过、依赖缺失等 6 类错误
- **多重修复策略**：指数退避重试 → 编码 Agent 修改代码 → RepairAgent 反思修复 → 历史案例参考修复 → 最终阻塞
- **进化自愈**：每次修复成功自动记录到案例库，后续遇到相似错误时优先参考历史成功经验
- **安全护栏**：总修复轮次上限 8 次，修复后强制审查验证，所有修复动作完整记录到日志
- **前端可见**：修复中显示 🔧 旋转图标和"系统正在尝试自动修复…"文字，修复成功后卡片变为绿色并标注"自动修复成功"

### 智能规划与多方案对比
- PM Agent 同时生成**方案A/方案B**两种架构思路，附带结构化优劣势对比
- 前端方案对比卡片展示，用户可选择方案A或方案B（120s 超时自动选A）
- 备选方案提供不同的技术选型或模块划分方式

### 依赖智能推断与门禁
- 上下文分析后自动推断项目需要的第三方库，生成 `dependencies.yaml` 草案
- 编码 Agent 严格基于确认的依赖清单编写代码，避免引入未声明依赖
- 前端展示依赖清单表格（库名、版本、用途、是否必需）

### 版本化交付与回滚
- 每次交付创建版本化目录 `deliveries/{pid}/v1/`, `v2/`...
- 保留最近 5 个版本，更旧版本自动归档为 ZIP
- `POST /api/projects/{id}/rollback` 支持一键回滚
- 前端版本树显示所有历史版本，支持查看和回滚操作

### 反馈学习与持续进化
- `POST /api/projects/{id}/feedback` 记录人工修改（原始代码→修改后→修改类型）
- 人工修改存入 `human_fixes.json`，自动检索注入到 Agent Prompt 中
- 每积累 20 条修改自动生成偏好学习报告
- 系统从"通用开发助手"进化为"个人专属开发伙伴"

### 自治项目孵化（Autopilot）
每个由 DevFlow CI 生成的项目都天然内置一套自治运维骨架（`autopilot/` 目录）：
- **health_check.py** — JSON 健康检查端点和 K8s 探针
- **logger.py** — 结构化 JSON 日志 + 可选远程上报
- **self_healing.py** — 基于 LLM API 的自动修复引擎 + 本地修复案例库
- **feature_flag.py** — 特性开关管理（支持灰度发布和白名单）
- **usage_analytics.py** — 匿名使用分析 + 周期性优化洞察报告
- 所有组件通过环境变量控制开关，默认不收集任何数据

### 自动化测试与质量验证
系统在多个层级对代码进行实际的运行验证，确保交付质量：
- **模块级测试执行**：LLM 生成测试后，在隔离沙箱中运行 pytest，失败则触发自愈修复
- **集成测试**：组装完成后运行跨模块集成测试，结果反馈给全局审查 Agent
- **交付前全量测试**：打包 ZIP 前运行所有测试，生成 `test_report.json`
- **降级保障**：pytest 不可用时自动降级为语法检查，不中断流程
- **前端可视化**：模块卡片显示测试徽章，交付时展示质量摘要弹窗

## API

完整参考见 [docs/API.md](docs/API.md)。主要端点：

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/projects` | 创建项目（requirement, directory?, mode） |
| GET | `/api/projects/{id}` | 项目状态 |
| GET | `/api/projects/{id}/logs` | SSE 日志 |
| GET | `/api/projects/{id}/stream-ai` | SSE AI 流 |
| POST | `/api/projects/{id}/confirm_plan` | 确认对齐/规划 |
| GET | `/api/projects/{id}/download` | 下载 ZIP |
| GET | `/api/projects/{id}/versions` | 版本列表 |
| POST | `/api/projects/{id}/rollback` | 版本回滚 |
| POST | `/api/projects/{id}/feedback` | 人工反馈 |
| GET | `/api/health` | 健康检查 |

## 项目结构

```
DevFlowCI/
├── main.py                   # FastAPI 入口
├── config.py                 # 全局配置
├── agents/                   # AI Agent 层（8 个 Agent）
├── workflow/                 # 工作流引擎
│   ├── executor.py           # 执行器
│   ├── langgraph_def.py      # LangGraph 图
│   ├── auto_fix.py           # 自愈引擎
│   ├── test_runner.py        # pytest 沙箱
│   ├── stream_relay.py       # AI 流式输出
│   └── error_logger.py       # 错误日志
├── memory/                   # 案例库 + 项目记忆
├── database/                 # SQLAlchemy + SQLite
├── static/                   # 前端 UI
├── docs/                     # 架构、API、决策、教训
├── scripts/                  # 会话启动/结束脚本
├── sessions/                 # 会话总结归档
├── templates/autopilot/        # 生成项目运维模板
└── deliveries/               # 交付物输出
```

## 测试

```bash
# 服务启动后
python test_flow.py    # 冒烟（推荐）
python test_e2e.py     # 完整 E2E
```

## 技术栈

- **后端**: Python 3.11+, FastAPI 0.115, LangGraph 0.2.56
- **LLM**: DeepSeek (via langchain-openai 0.2.14)
- **数据**: SQLAlchemy 2.0 + aiosqlite
- **前端**: 纯 HTML/CSS/JS (暗色主题)

## License

MIT — 见 [LICENSE](LICENSE)
