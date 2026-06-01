# DevFlow CI 记忆文件索引

> 最后更新：2026-06-01（会话 20260601-01）
> 权威来源规则见 [docs/decisions.md §决策 #018](decisions.md#决策-018docs-为记忆文件权威源根目录保留指针文件)

## 读取顺序（AI 会话启动 §2.3）

| 步骤 | 文件 | 用途 |
|------|------|------|
| 1 | `AI_COLLABORATION_GUIDE.md` | 协作规则（根目录） |
| 2 | `COLLABORATION_PROMPT.md` | AI 行为准则（根目录） |
| 3 | `CURRENT_STATUS.md` | 项目现状快照（根目录） |
| 4 | `docs/ARCHITECTURE.md` | 系统架构 |
| 5 | `docs/CODING_STANDARDS.md` | 编码规范 |
| 6 | `docs/LEARNINGS.md` | 历史教训（最近 20 条） |
| 7 | `docs/AGENT_PROMPT_TEMPLATES.md` | Agent Prompt + 任务模板 |
| 8 | `docs/decisions.md` | 架构决策（最近 30 天） |
| 9 | `docs/success_cases.json` | 可复用成功模式 |
| 10 | `BUG_TRACKER.md` | 已知缺陷（根目录） |
| 11 | `docs/DEVELOPMENT.md` | 本地开发指南 |
| 12 | `docs/API.md` | REST API 参考 |
| 13 | `sessions/<最新>/summary.md` | 上次会话总结 |

## 文件位置一览

| 文件 | 权威路径 | 根目录指针 |
|------|----------|------------|
| 项目状态 | `CURRENT_STATUS.md` | — |
| 架构决策 | `docs/decisions.md` | `decisions.md` → 指针 |
| 经验教训 | `docs/LEARNINGS.md` | `LEARNINGS.md` → 指针 |
| Prompt 模板 | `docs/AGENT_PROMPT_TEMPLATES.md` | `AGENT_PROMPT_TEMPLATES.md` → 指针 |
| 成功案例 | `docs/success_cases.json` | `success_cases.json` → 指针 |
| 系统架构 | `docs/ARCHITECTURE.md` | — |
| 编码规范 | `docs/CODING_STANDARDS.md` | — |
| 变更记录 | `CHANGELOG.md` | — |
| 缺陷追踪 | `BUG_TRACKER.md` | — |
| 会话归档 | `sessions/<YYYYMMDD-NN>/summary.md` | — |

## 写入规则

| 事件 | 写入目标 |
|------|----------|
| 会话结束 | `sessions/<id>/summary.md` + `CURRENT_STATUS.md` |
| 新决策 | `docs/decisions.md` |
| Bug 修复 / 踩坑 | `docs/LEARNINGS.md` + 必要时 `BUG_TRACKER.md` |
| 成功交付模式 | `docs/success_cases.json` |
| Prompt 改进 | `docs/AGENT_PROMPT_TEMPLATES.md` |
| 架构变更 | `docs/ARCHITECTURE.md` |

## 健康检查

运行 `scripts/session_start.sh` 或 `scripts/session_start.bat` 可自动检查记忆文件是否存在及行数。
