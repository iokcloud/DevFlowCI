# 项目当前状态

> 最后更新：2026-06-01（迭代工作流 + 文档同步 + 会话 checkpoint）
> 更新原因：P0/P1 迭代与文档同步落地；新增 HANDOFF / session_checkpoint 衔接新 Chat

<!-- AUTO-CHECKPOINT:START -->
> **自动 checkpoint** — 2026-06-02 07:57:09 · 会话 `20260602-01` · 分支 `master` · 工作区 **有未提交改动**

**下一 Chat 目标（建议）**：按需 commit 或 iterate 验证

**快速入口**：`HANDOFF.md` · `sessions/20260602-01/checkpoint.md` · `sessions/20260602-01/summary.md`

**Git 摘要**：
```
ab8662f feat(ux): 操作建议条、历史回顾分流与同目录智能跳转
24f31e7 release v0.4.9: 全 Pro 对齐 + 历史删除 + 资料目录扫描
6d944cb docs: release v0.4.8 — 分 Agent + SSE snapshot + 全 Pro 默认
```

**未提交**：
```
M CURRENT_STATUS.md
 M agents/alignment_agent.py
 M agents/business_planner.py
 M agents/integrator.py
 M database/db.py
 M database/models.py
 M docs/API.md
 M docs/MEMORY_INDEX.md
 M docs/decisions.md
 M docs/success_cases.json
 M main.py
 M scripts/session_start.bat
 M scripts/stop_server.bat
 M start.bat
 M static/app.js
 M static/index.html
 M static/style.css
 M static/ux-polish.css
 M static/ux-states.js
 M tests/test_executor.py
 M tests/test_integrator.py
 M tests/test_learnings_cluster.py
 M tests/test_project_delete.py
 M workflow/closed_loop.py
 M workflow/executor.py
 M workflow/project_snapshot.py
?? .cursor/
?? docs/templates/
?? memory/project_memory/
?? scripts/gen_favicon.py
?? scripts/session_checkpoint.bat
?? scripts/session_checkpoint.py
?? scripts/stop_server.ps1
?? static/favicon.ico
?? tests/test_closed_loop_review_callback.py
?? tests/test_delivery_paths.py
?? tests/test_delivery_suggestions.py
?? tests/test_display_name.py
?? tests/test_document_sync.py
?? tests/test_iteration.py
?? tests/test_requirement_context.py
?? workflow/delivery_paths.py
?? workflow/delivery_suggestions.py
?? workflow/document_sync.py
?? workflow/project_cleanup.py
?? workflow/requirement_context.py
?? workflow/state_builder.py
```
<!-- AUTO-CHECKPOINT:END -->

## 基本信息

- **当前阶段**：迭代工作流 + 文档同步（P0/P1）已落地，**工作区可能有大量未 commit 改动**
- **Git 分支**：见 HANDOFF.md / checkpoint
- **服务**：本地 `start.bat` → http://127.0.0.1:8000

## 本阶段已完成（近期）

- [x] 迭代 API：`POST /iterate`、`/finalize`、需求补充 `requirement_addendum`
- [x] 前端：继续迭代弹窗、定稿、历史状态同步
- [x] 文档同步 P0：`PRODUCT.md`、`DELIVERY_PLAN.md`、`CHANGELOG.md`、`QUALITY_REPORT.md`
- [x] 文档同步 P1：`modules/*.md`、`ACCEPTANCE.md`、定稿写 `LEARNINGS`
- [x] 全局审查 prompt 增强（减少「未提供 README」误报）
- [x] 会话衔接：`HANDOFF.md`、`session_checkpoint.ps1`、`.cursor/rules/session-handoff.mdc`

## 正在进行 / 下一步

1. [ ] **commit** 上述改动（用户明确要求后再做）
2. [ ] 重启服务，对 blocked 项目试跑 **继续迭代**
3. [ ] 确认 weekly E2E / 真实业务试跑

## 关键路径（给 Agent）

| 主题 | 路径 |
|------|------|
| 迭代 API | `main.py` — `/iterate`、`/finalize` |
| 文档同步 | `workflow/document_sync.py` |
| 状态重建 | `workflow/state_builder.py` |
| 前端 | `static/app.js`、`static/ux-states.js` |
| 测试 | `tests/test_document_sync.py`、`tests/test_iteration.py` |
| **新 Chat 衔接** | `HANDOFF.md`、`scripts/session_checkpoint.bat` |

## 刻意不做 / 已知约束

- **暂缓**：一键 autopilot、无 scope 全量重跑
- **审查误报**：已增强 prompt；仍可能有截断相关提示
- **换 Chat**：先跑 checkpoint，再复制 HANDOFF 起手式

## 模块完成度（简）

| 模块 | 状态 | 备注 |
|------|------|------|
| 迭代工作流 | 已实现 | 待实项目验证 |
| 文档同步 | P0+P1 | 定稿写 ACCEPTANCE/LEARNINGS |
| Web UI | 已调整 | 开始按钮、无完成弹窗、迭代 UI |
| CI/E2E | 沿用 v0.4.9 基线 | 见下方历史 Pro multi 记录 |

## Multi E2E 模型对比（历史参考，cap=2）

| 运行 | 模型 | 结果 |
|------|------|------|
| Pro #1–#3 | v4-pro 分 Agent | ✅ 2/2 连续通过 |

## 生产模型策略

- 默认 **deepseek-v4-pro** + 分 Agent + `REASONING_EFFORT=high`
