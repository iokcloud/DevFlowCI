# 本地开发指南

> 版本：v0.4.0 | 最后更新：2026-06-01

## 环境要求

- Python 3.11+
- DeepSeek API Key
- Windows：Git Bash 或 PowerShell

## 首次 setup

```bash
# 1. 虚拟环境
python -m venv venv

# Windows Git Bash
source venv/Scripts/activate

# 2. 依赖
pip install -r requirements.txt

# 3. 环境变量
cp .env.example .env
# 编辑 .env，填入 DEEPSEEK_API_KEY
```

## 启动服务

```bash
python main.py
# 或
./start.bat
```

浏览器访问：http://127.0.0.1:8000

## AI 协作会话

```bash
# Windows
scripts\session_start.bat

# Git Bash
bash scripts/session_start.sh
```

会话结束前运行 `scripts/session_end.bat <SESSION_ID>` 做质量检查。

记忆文件索引见 [MEMORY_INDEX.md](MEMORY_INDEX.md)。

## 测试

### 前置条件

1. 服务已启动（`python main.py`）
2. `.env` 中已配置有效的 `DEEPSEEK_API_KEY`

### 冒烟测试（推荐）

```bash
python test_flow.py
```

自动创建 is_prime 项目，并在 `aligned` / `plan_ready` 时自动确认。

### 完整 E2E

```bash
python test_e2e.py
```

包含简单需求、复杂需求、ZIP 下载验证三个场景。

## 目录约定

| 路径 | 用途 |
|------|------|
| `deliveries/` | 生成项目输出（gitignore） |
| `database/` | SQLite 数据库 |
| `memory/` | 案例库、项目记忆 |
| `docs/` | 架构、决策、教训等记忆文件 |
| `sessions/` | 会话总结（summary.md 纳入 git） |
| `scripts/` | 会话启动/结束脚本 |

## 常见问题

| 问题 | 处理 |
|------|------|
| 创建项目后卡在 `aligned` | 需调用 `confirm_plan` 或在前端确认；test_flow.py 会自动处理 |
| pytest 不可用 | 安装 `pip install pytest`；未安装时系统降级为语法检查 |
| API 连接失败 | 检查 `DEEPSEEK_BASE_URL` 是否含 `/v1` |
| 端口占用 | 修改启动命令中的 `--port` 或关闭占用 8000 的进程 |

## 文档导航

| 读者 | 入口 |
|------|------|
| 新用户 | [README.md](../README.md) |
| API 集成 | [API.md](API.md) |
| AI 协作 | [AI_COLLABORATION_GUIDE.md](../AI_COLLABORATION_GUIDE.md) |
| 架构 | [ARCHITECTURE.md](ARCHITECTURE.md) |
| 编码规范 | [CODING_STANDARDS.md](CODING_STANDARDS.md) |
