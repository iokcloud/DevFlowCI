# DevFlow CI API 参考

> 版本：v0.4.0 | 基础 URL：`http://127.0.0.1:8000`

## 项目

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/projects` | 创建项目，进入需求对齐 |
| GET | `/api/projects/history` | 历史项目列表 |
| GET | `/api/projects/{id}` | 项目状态与模块详情 |
| GET | `/api/projects/{id}/logs` | SSE 实时日志 |
| GET | `/api/projects/{id}/stream-ai` | SSE AI token 流 |
| POST | `/api/projects/{id}/confirm_plan` | 确认对齐（aligned）或规划（plan_ready） |
| POST | `/api/projects/{id}/cancel` | 终止任务 |
| GET | `/api/projects/{id}/download` | 下载交付 ZIP |
| GET | `/api/projects/{id}/versions` | 版本列表 |
| POST | `/api/projects/{id}/rollback` | 版本回滚 |
| POST | `/api/projects/{id}/feedback` | 人工修改反馈 |
| GET | `/api/projects/{id}/preferences` | 偏好学习报告 |

### POST `/api/projects`

```json
{
  "requirement": "写一个 is_prime 函数",
  "directory": null,
  "mode": "auto",
  "force_new": false
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| requirement | string | 自然语言需求（可与 directory 二选一） |
| directory | string? | 已有项目绝对路径 |
| mode | string | `auto` / `technical` / `business` |
| force_new | bool | 跳过去重，强制新建 |

### POST `/api/projects/{id}/confirm_plan`

对齐阶段（status=`aligned`）：

```json
{ "plan_choice": "A" }
```

规划阶段（status=`plan_ready`）：

```json
{}
```

可选传入修改后的 `modules`、`dependencies_override`。

## 文件系统

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/fs/drives` | Windows 盘符列表 |
| GET | `/api/fs/browse` | 浏览目录（query: path） |
| GET | `/api/fs/quick-access` | 快捷目录 |

## 维护

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/maintenance/error-stats` | 错误统计 |
| POST | `/api/maintenance/analyze-errors` | LLM 批量错误分析 |
| POST | `/api/maintenance/clean-logs` | 清理已解决日志 |
| GET | `/api/maintenance/delivery-suggestions` | 扫描 deliveries 可删除建议 |
| POST | `/api/maintenance/delivery-cleanup` | 按建议路径删除冗余交付物 |

## 其他

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/` | 前端 UI |
| GET | `/api/health` | 健康检查 |

## 状态机

```
created → aligning → aligned → planning → plan_ready → executing
  → integrating → reviewing → completed
```

终态还包括：`failed`、`needs_review`、`cancelled`。

对齐阶段（`aligned`）需用户调用 `confirm_plan` 后才会继续。
