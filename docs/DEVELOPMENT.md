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

停止服务（含 `--reload` 孤儿 worker）：

```bash
scripts\stop_server.bat
```

浏览器访问：http://127.0.0.1:8000

> 修改 `workflow/` 或 `main.py` 后请重启服务（Ctrl+C 后重新 `start.bat`），否则 E2E 测试可能仍跑旧逻辑。

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

1. **另开终端**启动服务：`start.bat` 或 `python main.py`
2. `.env` 中已配置有效的 `DEEPSEEK_API_KEY`

### 冒烟测试（推荐）

```bash
# Windows CMD / PowerShell（推荐，无需写 .exe 路径）
scripts\test_flow.bat

# 或激活 venv 后直接：
python test_flow.py
```

> **关于 `.exe` 后缀**：在 Windows 上 `python` 与 `python.exe` 等价（激活 venv 后均可）。
> 不要写 `venv\Scripts\python.exe test_flow.py` 这类绝对路径——不是卡住的原因，但不便移植。
> 若终端报编码错误，请用 `scripts\test_flow.bat`（已设 UTF-8）或 Git Bash。

测试默认最长等待 **900 秒**（LLM 多轮调用较慢）。每 15 秒会输出心跳，状态不变不代表卡死。

环境变量：`TEST_FLOW_TIMEOUT`、`TEST_FLOW_HEARTBEAT`、`TEST_FLOW_POLL_SEC`

### 完整 E2E

```bash
python test_e2e.py
```

### 单元测试（无需启动服务）

```bash
# 快测（与 PR CI 一致，50 项）
pytest tests/ -m "not integration" -v

# 全量（含 5 项集成测试，55 项）
pytest tests/ -v
```

CI 策略：PR 只跑 `-m "not integration"`；合并到 `master` 后额外跑集成测试。见 `.github/workflows/ci.yml`。

集成测试（mock LLM，无需 API Key）：

```bash
pytest tests/ -m integration -v
```

### 分支保护（master）

仓库已启用：CI 检查 `test` 必须通过 + 需通过 Pull Request 合并（无需他人 approve）。

重新配置（需 `gh` + git 凭据）：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/setup_branch_protection.ps1
```

日常合并流程：新建分支 → push → `gh pr create` → CI 绿后 merge。

### 记忆检索

```bash
python scripts/memory_search.py "关键词"
python scripts/memory_search.py "自愈" --source learnings --top 5
```

### Nightly E2E

仓库含 `.github/workflows/nightly-e2e.yml`（每日定时 + 手动触发）。

在 GitHub **Settings → Secrets → Actions** 配置 `DEEPSEEK_API_KEY` 后才会真正跑 `test_flow.py`；未配置时 workflow 会跳过并 warning。

提交前可选：

```bash
scripts\pre_commit_check.bat
# 或 Git Bash
bash scripts/pre_commit_check.sh
```

安装 git pre-commit 钩子（提交前自动跑上述检查）：

```bash
scripts\install_hooks.bat
# 或 Git Bash
bash scripts/install_hooks.sh
```

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
| 端口占用 | 修改启动命令中的 `--port`；Windows 上 `--reload` 父进程被杀后子 worker 可能仍占用 8000，用 `netstat -ano \| findstr :8000` 找到 PID 后 `taskkill /F /PID <pid>` |

## 文档导航

| 读者 | 入口 |
|------|------|
| 新用户 | [README.md](../README.md) |
| API 集成 | [API.md](API.md) |
| AI 协作 | [AI_COLLABORATION_GUIDE.md](../AI_COLLABORATION_GUIDE.md) |
| 架构 | [ARCHITECTURE.md](ARCHITECTURE.md) |
| 编码规范 | [CODING_STANDARDS.md](CODING_STANDARDS.md) |
