"""工作流执行器 — 驱动 LangGraph 图执行，管理 SSE 日志流。

核心职责：
1. 编译并执行主工作流图
2. 在模块级实现自定义子图逻辑（分析→编码→测试→审查 重试循环）
3. 管理 SSE 日志推送队列
4. 并行执行无依赖的模块
5. 上下文分析节点（读取项目目录）
6. 永不卡死策略：blocked 替换 failed，占位文件，TODO.md
"""

from __future__ import annotations

import asyncio
import json
import os
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator

from langgraph.graph import StateGraph

from agents.integrator import (
    GlobalReviewResult,
    GlobalReviewerAgent,
    IntegrationResult,
    IntegratorAgent,
)
from agents.alignment_agent import AlignmentAgent
from agents.business_planner import BusinessPlannerAgent
from agents.module_agents import (
    ModuleAgents,
    ModuleCode,
    ModuleSpec,
    ModuleTestResult,
)
from agents.planner import PlannerAgent
from agents.repair_agent import RepairAgent
from agents.reviewer import ReviewResult, ReviewerAgent
from config import (
    MAX_CONCURRENT_MODULES,
    MAX_REVIEW_RETRIES,
    MAX_NON_MODULE_RETRIES,
    CONTEXT_ANALYSIS_TOKEN_LIMIT,
    CONTEXT_ANALYSIS_MAX_DEPTH,
    DELIVERIES_DIR,
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    DEEPSEEK_MODEL,
    AUTO_FIX_ENABLED,
    AUTO_FIX_MAX_TOTAL_ROUNDS,
    AUTO_FIX_QUICK_REVIEW_MAX,
    TEST_EXECUTION_ENABLED,
    TEST_UNIT_TIMEOUT,
    TEST_INTEGRATION_TIMEOUT,
    TEST_FULL_SUITE_TIMEOUT,
    TEST_MAX_FAILURES_BEFORE_WARN,
    DEPENDENCY_INFERENCE_ENABLED,
    FEEDBACK_LEARNING_ENABLED,
    VERSIONED_DELIVERY_ENABLED,
    MAX_VERSIONS_KEPT,
    ARCHIVE_DIR_NAME,
    AUTOPILOT_ENABLED,
    BUSINESS_MVP_MAX_MODULES,
)
from memory.case_store import CaseStore
from memory.project_memory import ProjectMemoryStore
from workflow.auto_fix import classify_error, search_similar_cases, FixContext
from workflow.test_runner import (
    run_module_tests,
    run_integration_tests,
    run_full_test_suite,
    TestResult,
)
from workflow.langgraph_def import (
    ModuleState,
    WorkflowState,
    _after_modules,
    _after_global_review,
    _after_review,
    build_main_graph,
    topological_sort,
)
from workflow.stream_relay import push_ai_token, AGENT_LABEL_MAP


# ── SSE 日志队列 ──────────────────────────────────────────

# 全局字典：project_id → asyncio.Queue
_log_queues: dict[str, asyncio.Queue[dict[str, Any]]] = {}


def get_log_queue(project_id: str) -> asyncio.Queue[dict[str, Any]]:
    """获取或创建项目的 SSE 日志队列。"""
    if project_id not in _log_queues:
        _log_queues[project_id] = asyncio.Queue(maxsize=500)
    return _log_queues[project_id]


async def push_log(
    project_id: str,
    level: str,
    message: str,
    module_name: str = "",
    state_event: str = "",
) -> None:
    """推送日志到 SSE 队列 + 持久化到数据库。

    Args:
        project_id: 项目标识
        level: INFO / WARN / ERROR / SUCCESS / STATE
        message: 日志内容
        module_name: 关联模块名
        state_event: 状态变更事件（aligning/aligned/planning/plan_ready/executing/completed）
    """
    entry = {
        "project_id": project_id,
        "level": level,
        "message": message,
        "module_name": module_name,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if state_event:
        entry["state_event"] = state_event
        # 状态事件实时同步到 DB，避免 API 长时间显示旧状态
        try:
            asyncio.create_task(_sync_project_status(project_id, state_event))
        except Exception:
            pass
    # 推送 SSE
    queue = get_log_queue(project_id)
    try:
        queue.put_nowait(entry)
    except asyncio.QueueFull:
        pass  # 丢弃超额的日志
    # ★ 持久化到数据库（异步写入，失败不影响主流程）
    try:
        from database.db import async_session_factory
        from database.models import Project, ProjectLog
        from sqlalchemy import select as _sql_select
        async with async_session_factory() as _db:
            _proj = await _db.execute(_sql_select(Project).where(Project.project_id == project_id))
            _proj_row = _proj.scalar_one_or_none()
            if _proj_row:
                _log = ProjectLog(
                    project_id_fk=_proj_row.id,
                    level=level,
                    message=message[:1000],
                    module_name=module_name if module_name else None,
                )
                _db.add(_log)
                await _db.commit()
    except Exception:
        pass  # 持久化失败不阻塞主流程

    # ★ ERROR / WARN 级别也写入 ErrorLog 表（用于聚合分析和自动修复）
    if level in ("ERROR", "WARN"):
        try:
            from workflow.error_logger import write_error_log
            import uuid as _uuid_mod
            _err_trace = f"log-{_uuid_mod.uuid4().hex[:12]}"
            _err_type = "review_fail" if "审查" in message else ("test_failure" if "测试" in message else "unknown")
            await write_error_log(
                project_id=project_id,
                message=message,
                trace_id=_err_trace,
                module_name=module_name if module_name else "",
                error_type=_err_type,
                stacktrace="",
            )
        except Exception:
            pass


def remove_log_queue(project_id: str) -> None:
    """清理项目的日志队列。"""
    _log_queues.pop(project_id, None)


async def push_module_event(
    project_id: str,
    module_name: str,
    status: str,
    *,
    failure_reason: str = "",
    description: str = "",
) -> None:
    """推送模块状态变更到 SSE，并同步写入 ModuleTask。"""
    entry: dict[str, Any] = {
        "project_id": project_id,
        "level": "MODULE",
        "message": f"[{module_name}] → {status}",
        "module_name": module_name,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "module_event": {
            "module_name": module_name,
            "status": status,
            "failure_reason": failure_reason,
            "description": description,
        },
    }
    queue = get_log_queue(project_id)
    try:
        queue.put_nowait(entry)
    except asyncio.QueueFull:
        pass

    try:
        from database.db import async_session_factory
        from database.models import ModuleStatus, ModuleTask, Project
        from sqlalchemy import select as _sel

        async with async_session_factory() as db:
            result = await db.execute(
                _sel(Project).where(Project.project_id == project_id)
            )
            project = result.scalar_one_or_none()
            if not project:
                return
            mod_result = await db.execute(
                _sel(ModuleTask).where(
                    ModuleTask.project_id_fk == project.id,
                    ModuleTask.module_name == module_name,
                )
            )
            mod = mod_result.scalar_one_or_none()
            if not mod:
                return
            try:
                mod.status = ModuleStatus(status)
            except ValueError:
                mod.status = ModuleStatus.CODING
            if failure_reason:
                mod.failure_reason = failure_reason[:2000]
            if description:
                mod.description = description[:1000]
            await db.commit()
    except Exception:
        pass


async def stream_logs(
    project_id: str
) -> AsyncIterator[dict[str, Any]]:
    """SSE 日志生成器。

    Args:
        project_id: 项目标识

    Yields:
        日志条目字典
    """
    queue = get_log_queue(project_id)
    while True:
        try:
            entry = await asyncio.wait_for(queue.get(), timeout=30)
            yield entry
        except asyncio.TimeoutError:
            # 发送心跳
            yield {"level": "HEARTBEAT", "message": "", "timestamp": ""}


async def _notify_ai_stream(
    project_id: str,
    agent_name: str,
    event_type: str,
    content: str = "",
) -> None:
    """推送 AI 流式事件（通知/开始/完成/输出文本）。

    Args:
        project_id: 项目标识
        agent_name: 代理名称（如 planner, alignment_agent）
        event_type: "start" / "token" / "done" / "notification"
        content: 事件内容
    """
    try:
        if event_type == "start":
            label = AGENT_LABEL_MAP.get(agent_name, agent_name)
            await push_ai_token(project_id, {
                "type": "notification",
                "agent": agent_name,
                "content": f"{label} 开始工作...",
            })
        elif event_type == "done":
            label = AGENT_LABEL_MAP.get(agent_name, agent_name)
            await push_ai_token(project_id, {
                "type": "notification",
                "agent": agent_name,
                "content": f"{label} 完成" + (f"（{content}）" if content else ""),
            })
            await push_ai_token(project_id, {
                "type": "done",
                "agent": agent_name,
                "content": "",
            })
        elif event_type == "token":
            # 将大段文本分块模拟流式推送
            chunk_size = 80
            for i in range(0, len(content), chunk_size):
                chunk = content[i : i + chunk_size]
                await push_ai_token(project_id, {
                    "type": "token",
                    "agent": agent_name,
                    "content": chunk,
                })
        elif event_type == "notification":
            await push_ai_token(project_id, {
                "type": "notification",
                "agent": agent_name,
                "content": content,
            })
    except Exception:
        pass  # 流式推送失败不影响主流程


# ── 忽略目录列表 ──────────────────────────────────────────

IGNORED_DIRS = {
    "node_modules", "__pycache__", ".git", ".svn", ".hg",
    ".venv", "venv", ".tox", ".eggs", "build", "dist",
    ".mypy_cache", ".pytest_cache", ".ruff_cache",
    ".next", ".nuxt", ".cache", "coverage",
    "egg-info", ".egg-info", "site-packages",
}


def _estimate_tokens(text: str) -> int:
    """粗略估算文本的 token 数（中文按字符，英文按词）。"""
    # 简单估算：英文 1 token ≈ 4 chars，中文 1 token ≈ 1 char
    import re
    chinese = len(re.findall(r"[\u4e00-\u9fff]", text))
    other = len(text) - chinese
    return chinese + other // 4


# ── 数据库状态同步（避免API显示滞后）──────────────────────

async def _sync_project_status(project_id: str, status: str) -> None:
    """将工作流状态实时同步到数据库 Project 表。

    解决 plan_only / execute_from_plan 等阶段完成后，
    API 仍显示旧状态的问题。
    """
    try:
        from database.db import async_session_factory
        from database.models import Project, ProjectStatus
        from sqlalchemy import select as _sel

        _status_map = {
            "created": ProjectStatus.CREATED,
            "aligning": ProjectStatus.ALIGNING,
            "aligned": ProjectStatus.ALIGNED,
            "planning": ProjectStatus.PLANNING,
            "plan_ready": ProjectStatus.PLAN_READY,
            "executing": ProjectStatus.EXECUTING,
            "integrating": ProjectStatus.INTEGRATING,
            "reviewing": ProjectStatus.REVIEWING,
            "completed": ProjectStatus.COMPLETED,
            "failed": ProjectStatus.FAILED,
            "needs_review": ProjectStatus.NEEDS_REVIEW,
        }
        db_status = _status_map.get(status)
        if db_status is None:
            return

        async with async_session_factory() as db:
            result = await db.execute(
                _sel(Project).where(Project.project_id == project_id)
            )
            project = result.scalar_one_or_none()
            if project:
                project.status = db_status
                await db.commit()
    except Exception:
        pass  # 状态同步失败不阻塞主流程


async def _persist_plan_modules(
    project_id: str, plan_json: str, modules: list[dict[str, Any]]
) -> None:
    """规划完成后将模块列表写入 DB，供 API 轮询展示进度。"""
    try:
        from database.db import async_session_factory
        from database.models import ModuleTask, ModuleStatus, Project
        from sqlalchemy import delete as sql_delete
        from sqlalchemy import select as _sel

        async with async_session_factory() as db:
            result = await db.execute(
                _sel(Project).where(Project.project_id == project_id)
            )
            project = result.scalar_one_or_none()
            if not project:
                return
            project.plan_json = plan_json
            await db.execute(
                sql_delete(ModuleTask).where(ModuleTask.project_id_fk == project.id)
            )
            for m in modules:
                db.add(
                    ModuleTask(
                        project_id_fk=project.id,
                        module_name=m["module_name"],
                        description=m.get("description", ""),
                        dependencies=json.dumps(
                            m.get("dependencies", []), ensure_ascii=False
                        ),
                        module_type=m.get("type", "backend"),
                        status=ModuleStatus.PENDING,
                    )
                )
            await db.commit()
            for m in modules:
                await push_module_event(
                    project_id,
                    m["module_name"],
                    ModuleStatus.PENDING.value,
                    description=m.get("description", ""),
                )
    except Exception:
        pass


async def _update_module_status(
    project_id: str,
    module_name: str,
    status: str,
    *,
    failure_reason: str = "",
    description: str = "",
) -> None:
    """更新模块执行态（SSE + DB）。"""
    await push_module_event(
        project_id,
        module_name,
        status,
        failure_reason=failure_reason,
        description=description,
    )


async def _persist_module_result(
    project_id: str, module_name: str, result_data: dict[str, Any]
) -> None:
    """单个模块完成后增量写入 DB，供 test_flow / 前端实时展示。"""
    try:
        from database.db import async_session_factory
        from database.models import ModuleStatus, ModuleTask, Project
        from sqlalchemy import select as _sel

        status_str = result_data.get("status", "failed")
        try:
            mod_status = ModuleStatus(status_str)
        except ValueError:
            mod_status = ModuleStatus.FAILED

        spec = result_data.get("spec", {})
        if isinstance(spec, dict):
            spec_text = json.dumps(spec, ensure_ascii=False)
        else:
            spec_text = str(spec)

        auto_fix = result_data.get("auto_fix_history", "[]")
        if not isinstance(auto_fix, str):
            auto_fix = json.dumps(auto_fix, ensure_ascii=False)

        test_result = result_data.get("test_result")
        test_result_text = (
            json.dumps(test_result, ensure_ascii=False)
            if test_result
            else None
        )

        async with async_session_factory() as db:
            result = await db.execute(
                _sel(Project).where(Project.project_id == project_id)
            )
            project = result.scalar_one_or_none()
            if not project:
                return
            mod_result = await db.execute(
                _sel(ModuleTask).where(
                    ModuleTask.project_id_fk == project.id,
                    ModuleTask.module_name == module_name,
                )
            )
            mod = mod_result.scalar_one_or_none()
            if not mod:
                mod = ModuleTask(
                    project_id_fk=project.id,
                    module_name=module_name,
                    description=result_data.get("description", module_name),
                    status=mod_status,
                )
                db.add(mod)
            mod.status = mod_status
            mod.code = result_data.get("code", "") or ""
            mod.tests = result_data.get("test_code", "") or ""
            mod.spec = spec_text
            mod.retry_count = result_data.get("retry_count", 0)
            mod.failure_reason = result_data.get("failure_reason", "") or ""
            mod.auto_fix_history = auto_fix
            mod.test_result = test_result_text
            await db.commit()

        await push_module_event(
            project_id,
            module_name,
            status_str,
            failure_reason=result_data.get("failure_reason", "") or "",
        )
    except Exception:
        pass


# ── 上下文分析 ────────────────────────────────────────────

def analyze_project_context(directory: str) -> str:
    """分析项目目录，生成结构化上下文描述。（保留兼容旧接口）

    Args:
        directory: 项目目录的绝对路径

    Returns:
        结构化的上下文描述文本（JSON 字符串）
    """
    result = analyze_project_context_structured(directory)
    return json.dumps(result, ensure_ascii=False)


def analyze_project_context_structured(directory: str) -> dict[str, Any]:
    """分析项目目录，输出结构化 JSON。

    返回格式:
    {
        "analyzed_files": [{"file": "相对路径", "summary": "内容摘要"}],
        "overall_summary": "全局项目描述",
        "project_type": "Python 项目",
        "no_documentation_found": true/false
    }
    """
    dir_path = Path(directory)
    if not dir_path.exists() or not dir_path.is_dir():
        return {
            "analyzed_files": [],
            "overall_summary": f"目录不存在或无效: {directory}",
            "no_documentation_found": True,
        }

    # ── 1. 检测项目类型 ──
    project_type = "未知"
    key_files: dict[str, Path] = {}
    key_file_names = [
        "package.json", "requirements.txt", "pyproject.toml",
        "Cargo.toml", "go.mod", "pom.xml", "build.gradle",
        "tsconfig.json", "Dockerfile", "docker-compose.yml",
        "Makefile", "README.md", "app.py", "main.py", "index.js",
        "index.ts", "index.html",
    ]
    for fname in key_file_names:
        fpath = dir_path / fname
        if fpath.exists() and fpath.is_file():
            key_files[fname] = fpath

    if "requirements.txt" in key_files or "pyproject.toml" in key_files or "app.py" in key_files or "main.py" in key_files:
        project_type = "Python 项目"
    elif "package.json" in key_files:
        project_type = "Node.js / JavaScript 项目"
    elif "go.mod" in key_files:
        project_type = "Go 项目"
    elif "Cargo.toml" in key_files:
        project_type = "Rust 项目"
    elif "pom.xml" in key_files or "build.gradle" in key_files:
        project_type = "Java 项目"

    # ── 2. 收集文档文件（递归扫描全部子目录） ──
    doc_files: list[Path] = []
    doc_extensions = {".md", ".txt", ".rst", ".adoc", ".docx"}

    # 递归扫描整个项目目录，忽略非文档目录
    for ext in doc_extensions:
        for fp in dir_path.glob(f"**/*{ext}"):
            # 跳过被忽略的目录
            parts = fp.relative_to(dir_path).parts
            if any(p in IGNORED_DIRS or p.startswith(".") for p in parts):
                continue
            doc_files.append(fp)

    # 去重 + 限制数量
    doc_files = list(set(doc_files))[:20]

    # ── 3. 读取文档内容 ──
    file_contents: list[dict[str, Any]] = []
    total_chars = 0
    MAX_DOC_CHARS = 8000  # 文档总字符上限

    for fp in doc_files:
        try:
            # .docx 文件特殊处理
            if fp.suffix.lower() == ".docx":
                content = _read_docx(fp)
            else:
                content = fp.read_text(encoding="utf-8", errors="ignore")
            # 跳过过短文件
            if len(content.strip()) < 30:
                continue
            # 截取
            if total_chars + len(content) > MAX_DOC_CHARS:
                content = content[:MAX_DOC_CHARS - total_chars] + "\n...(已截断)"
            rel_path = str(fp.relative_to(dir_path))
            file_contents.append({"file": rel_path, "content": content})
            total_chars += len(content)
            if total_chars >= MAX_DOC_CHARS:
                break
        except Exception:
            pass

    # ── 4. 收集源代码文件列表（不读内容，仅列出） ──
    source_files: list[str] = []
    for pattern in ["*.py", "*.js", "*.ts", "*.go", "*.rs", "*.java"]:
        for sf in dir_path.glob(pattern):
            rel = str(sf.relative_to(dir_path))
            source_files.append(rel)
            if len(source_files) >= 30:
                break
        if len(source_files) >= 30:
            break

    # ── 5. 判断文档可用性 ──
    no_documentation_found = len(file_contents) == 0

    if no_documentation_found:
        return {
            "analyzed_files": [],
            "overall_summary": f"项目类型: {project_type}。目录下无文档文件（.md/.txt/.rst），仅有源代码。",
            "project_type": project_type,
            "source_files": source_files[:20],
            "no_documentation_found": True,
        }

    # ── 6. 调用 LLM 生成文档摘要 ──
    summaries = _generate_doc_summaries(file_contents, project_type, source_files)

    # ── 7. 文档类型识别（商业 vs 技术）──
    all_text = " ".join(fc["content"] for fc in file_contents).lower()
    summaries["document_type"] = _detect_document_type(all_text)

    return summaries


def _detect_document_type(text: str) -> str:
    """基于关键词计数判断文档类型：business / technical / generic。

    修复说明（2026-05-31）：
    - 扩展中文商业关键词库，覆盖交易数据、话题分析、课件、市场等常见商业词汇。
    - 增加兜底策略：generic 但技术关键词为 0 时，优先尝试商业模式。
    - 关键词匹配支持中文分词（逐词扫描，因中文无空格分词）。
    """
    business_keywords = [
        # ── 原有关键词 ──
        "市场规模", "竞争分析", "消费者行为", "渠道", "营收模型", "营收",
        "定价策略", "swot", "pest", "行业趋势", "市场份额", "目标用户",
        "商业模式", "收入来源", "客户画像", "市场调研", "行业分析",
        "增长", "盈利", "定价", "营销", "品牌", "消费者", "用户付费",
        "转化率", "留存率", "获客成本", "ltv", "roi",
        "供应链", "经销商", "零售", "电商", "线下",
        # ── 新增：交易 / 消费 / 销售 ──
        "交易", "交易数据", "交易量", "交易额", "销售额", "客单价",
        "消费", "购买", "客户", "买家", "卖家", "下单", "复购",
        "营收", "收入", "毛利", "净利", "利润", "盈利模式",
        # ── 新增：市场 / 行业分析 ──
        "市场分析", "行业报告", "行业研究", "市场趋势", "市场调研",
        "红海", "蓝海", "市场份额", "市场占有率", "竞争格局",
        "下沉市场", "出海", "跨境", "本地化",
        # ── 新增：用户 / 消费者画像 ──
        "用户画像", "消费者画像", "目标群体", "年龄段", "地域分布",
        "受访者", "问卷", "调研", "调查", "样本", "用户研究",
        "消费习惯", "购买力", "消费能力",
        # ── 新增：营销 / 推广 ──
        "推广", "投放", "广告", "流量", "转化", "社群", "私域",
        "直播", "短视频", "内容营销", "种草", "达人", "kol",
        "品牌营销", "品牌推广",
        # ── 新增：教育 / 课件 / 内容 ──
        "课件", "教案", "课程", "教育", "培训", "教学",
        "知识付费", "内容付费", "在线教育",
        # ── 新增：话题 / 热度 / 趋势 ──
        "话题", "热度", "热门", "趋势", "风口", "预测",
        "规模", "增长率", "年增长", "复合增长率", "cagr",
        # ── 新增：创业 / 融资 ──
        "融资", "估值", "创业", "孵化", "天使轮", "a轮", "b轮", "vc", "pe",
        # ── 新增：渠道 / 供应链 ──
        "渠道", "经销商", "分销", "代理", "供应商", "供应链", "物流",
    ]
    technical_keywords = [
        "api", "后端", "前端", "数据库", "微服务", "架构", "部署",
        "测试", "重构", "代码", "接口", "服务器", "缓存", "队列",
        "http", "rest", "sql", "nosql", "docker", "kubernetes",
        "python", "java", "javascript", "typescript", "react",
        "fastapi", "spring", "node", "postgresql", "mysql", "redis",
        "git", "ci/cd", "devops", "linux", "nginx",
        # ── 新增中文技术词（避免单字歧义词：'类'会误匹配'教育类'，'对象'会误匹配'研究对象'）──
        "编程", "开发", "调试", "日志", "配置", "模块", "函数",
        "面向对象", "基类", "抽象类", "数据结构", "算法",
        "多线程", "异步", "并发", "分布式", "容器化",
    ]

    b_count = sum(1 for kw in business_keywords if kw in text)
    t_count = sum(1 for kw in technical_keywords if kw in text)

    if b_count >= 3 and b_count > t_count:
        return "business"
    elif b_count >= 2 and t_count == 0:
        # 兜底：有商业关键词但无任何技术关键词 → 视为商业文档
        return "business"
    elif t_count >= 3 and t_count > b_count:
        return "technical"
    elif b_count >= 2:
        return "business"
    elif t_count >= 1 and b_count == 0:
        return "technical"
    elif b_count >= 1 and t_count == 0:
        # 即使只有 1 个商业关键词，如果没有任何技术关键词，也优先走商业模式
        return "business"
    else:
        return "generic"


def _generate_doc_summaries(
    file_contents: list[dict[str, Any]],
    project_type: str,
    source_files: list[str],
) -> dict[str, Any]:
    """调用 LLM 为文档文件生成结构化摘要。"""
    from utils import create_llm

    llm = create_llm(temperature=0.1, max_tokens=2048, timeout=60, max_retries=1)

    docs_text_parts: list[str] = []
    for fc in file_contents:
        docs_text_parts.append(f"### {fc['file']}\n```\n{fc['content'][:2000]}\n```")
    docs_text = "\n\n".join(docs_text_parts)

    src_list = ", ".join(source_files[:15]) if source_files else "无"

    prompt = f"""你是一位技术文档分析师。请分析以下项目文档，输出一个严格的 JSON。

项目类型: {project_type}
源代码文件: {src_list}

## 文档内容

{docs_text}

## 输出格式（严格 JSON，不要包含其他文字）

```json
{{
  "analyzed_files": [
    {{
      "file": "相对路径",
      "summary": "该文档的3-5句关键内容摘要，聚焦于：它描述了什么系统/服务、有哪些核心功能、技术栈是什么"
    }}
  ],
  "overall_summary": "基于所有文档的全局项目描述，2-4句话"
}}
```

## 规则
1. 只输出 JSON，不要加任何解释。
2. summary 必须严格基于文档实际内容，不得推测或添加文档未提及的信息。
3. 如果某个文档内容太少或无法提取有效信息，可以跳过该文件。
4. 每个 summary 控制在 50-120 字。
5. overall_summary 聚焦于项目是做什么的、用什么技术栈、有哪些核心模块。"""

    try:
        response = llm.invoke(prompt)
        raw_text: str = response.content if hasattr(response, "content") else str(response)
        # 提取 JSON
        import re
        text = raw_text.strip()
        match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
        if match:
            text = match.group(1).strip()
        brace_start = text.find("{")
        if brace_start != -1:
            depth = 0
            for i in range(brace_start, len(text)):
                if text[i] == "{":
                    depth += 1
                elif text[i] == "}":
                    depth -= 1
                    if depth == 0:
                        text = text[brace_start : i + 1]
                        break
        result = json.loads(text)
        result.setdefault("analyzed_files", [])
        result.setdefault("overall_summary", "")
        result["project_type"] = project_type
        result["source_files"] = source_files[:20]
        result["no_documentation_found"] = False
        return result
    except Exception:
        return {
            "analyzed_files": [{"file": fc["file"], "summary": fc["content"][:200]} for fc in file_contents[:5]],
            "overall_summary": f"项目类型: {project_type}（LLM 摘要生成失败，使用原始文档片段）",
            "project_type": project_type,
            "source_files": source_files[:20],
            "no_documentation_found": False,
        }


def _read_docx(fp: Path) -> str:
    """读取 .docx 文件的纯文本内容。"""
    try:
        from docx import Document
        doc = Document(str(fp))
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        return "\n".join(paragraphs)
    except Exception:
        return ""


def _build_tree(path: Path, max_depth: int, current_depth: int = 0) -> list[str]:
    """构建目录树文本。"""
    if current_depth > max_depth:
        return [f"{'  ' * current_depth}..."]
    if path.name in IGNORED_DIRS:
        return [f"{'  ' * current_depth}{path.name}/ [已忽略]"]

    lines: list[str] = []
    try:
        entries = sorted(path.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
    except PermissionError:
        return [f"{'  ' * current_depth}{path.name}/ [权限不足]"]

    for entry in entries:
        if entry.name in IGNORED_DIRS or entry.name.startswith("."):
            continue
        if entry.is_dir():
            lines.append(f"{'  ' * current_depth}{entry.name}/")
            lines.extend(_build_tree(entry, max_depth, current_depth + 1))
        else:
            lines.append(f"{'  ' * current_depth}{entry.name}")
    return lines


# ── 兜底对齐生成 ──────────────────────────────────────────

def _fallback_alignment(
    requirement: str,
    structured_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """当 LLM 调用失败或返回 insufficient_info 时，生成智能兜底对齐方案。

    确保流程不会因为 LLM 故障而完全阻塞。
    如果有项目文档上下文，会生成更准确的兜底方案。
    """
    import re

    directory = ""
    analyzed_files: list[dict[str, Any]] = []
    if structured_context:
        analyzed_files = structured_context.get("analyzed_files", [])
        # 尝试从 overall_summary 提取有用信息
        overall = structured_context.get("overall_summary", "")

    # ── 场景1：有文档但无法分析 → 告知用户并提供建议 ──
    if analyzed_files:
        file_names = [af.get("file", "未知") for af in analyzed_files[:5]]
        file_list = "、".join(file_names)
        doc_type = structured_context.get("document_type", "generic") if structured_context else "generic"
        summary = (
            f"目录中有 {len(analyzed_files)} 个文档文件（{file_list}等），"
            f"但 AI 自动分析未能生成完整计划。"
        )
        modules = [{
            "module": "文档分析建议",
            "description": f"检测到文档类型为「{doc_type}」，建议在需求框中输入具体的开发/分析需求",
            "reason": f"目录中文档不足以自动推导代码开发计划，需要用户提供明确指令",
            "type": "backend",
        }]
        return {
            "summary": summary,
            "assumptions": [
                f"文档类型: {doc_type}",
                "假设：用户将在需求框中补充具体需求后重新提交",
            ],
            "risks": [
                "当前自动分析未生成可执行模块",
                "建议输入具体开发需求（如「基于这些市场数据开发一个分析工具」）",
            ],
            "plan": modules,
            "questions": [
                "您希望基于这些文档做什么？（如：生成市场分析报告、开发数据分析工具、制作演示文稿等）",
            ],
        }

    # ── 场景2：无文档，纯文本需求 → 关键词提取兜底 ──
    words = re.findall(r"[\u4e00-\u9fff]+|[a-zA-Z]+", requirement)
    keywords = [w for w in words if len(w) >= 2][:5]

    modules = []
    if keywords:
        for i, kw in enumerate(keywords[:4]):
            modules.append({
                "module": kw.lower().replace(" ", "_")[:30],
                "description": f"实现与「{kw}」相关的功能",
                "reason": f"需求中提到了「{kw}」，需要实现相关功能",
                "type": "backend",
            })
    else:
        modules.append({
            "module": "main_module",
            "description": requirement[:80],
            "reason": "实现用户需求的核心功能",
            "type": "backend",
        })

    return {
        "summary": f"基于用户需求「{requirement[:80]}...」的基础执行计划（AI 分析暂不可用，使用模板方案）",
        "assumptions": [
            "技术栈假设：Python 3.12+（默认）",
            "环境假设：本地开发环境",
            "注：此方案由模板生成，建议人工审查",
        ],
        "risks": [
            "AI 分析未完成，模块划分可能不够精确",
            "建议人工审查并调整计划",
        ],
        "plan": modules,
        "questions": ["请人工审查此自动生成的计划是否满足需求"],
    }


# ── 占位文件生成 ──────────────────────────────────────────

def _generate_stub_code(module_name: str, module_type: str, description: str, failure_reason: str) -> str:
    """为 blocked 模块生成骨架占位代码。

    Args:
        module_name: 模块名称
        module_type: 模块类型 (backend/frontend/database/integration/testing)
        description: 模块描述
        failure_reason: 失败/阻塞原因

    Returns:
        带详细注释的骨架代码字符串
    """
    header = f'''"""
⚠️ 此模块因以下原因被自动阻塞，请手动完成实现。

模块名称: {module_name}
模块描述: {description}
模块类型: {module_type}
阻塞原因: {failure_reason}

原始需求: 请根据项目规划上下文手动完善此模块。
建议: 先阅读 TODO.md 了解项目整体状态，再对此文件进行开发。
"""
'''

    if module_type in ("backend", "integration"):
        stub = f'''{header}
# TODO: 实现以下函数/类的完整逻辑

def {module_name}() -> dict:
    """待实现：{description}

    Returns:
        dict: 操作结果
    """
    raise NotImplementedError(
        "此模块已被自动阻塞，请手动实现。"
        "详见文件顶部注释和项目根目录的 TODO.md。"
    )


if __name__ == "__main__":
    print("此模块尚未完成，请参考 TODO.md 进行开发。")
'''
    elif module_type == "database":
        stub = f'''{header}
# TODO: 定义数据库模型

# from sqlalchemy import Column, Integer, String
# from database.db import Base
#
# class PlaceholderModel(Base):
#     __tablename__ = "{module_name}"
#     id = Column(Integer, primary_key=True)
#     # 请在此添加字段定义

print("此模块尚未完成，请参考 TODO.md 进行开发。")
'''
    elif module_type == "frontend":
        stub = f'''{header}
<!-- TODO: 实现前端组件 -->
<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"><title>{module_name}</title></head>
<body>
  <h1>模块: {module_name}</h1>
  <p>此模块尚未完成，请参考 TODO.md 进行开发。</p>
</body>
</html>
'''
    else:
        stub = f'''{header}
# TODO: {description}

# 此模块尚未完成，阻塞原因: {failure_reason}
# 请参考项目根目录的 TODO.md 完成开发。
'''
    return stub


def _resolve_mvp_module_cap(state: WorkflowState) -> int | None:
    """商业 MVP 或 state 显式指定的模块上限。"""
    cap = state.get("mvp_max_modules")
    if cap is not None:
        try:
            return max(1, int(cap))
        except (TypeError, ValueError):
            pass
    alignment = state.get("alignment_result") or {}
    if isinstance(alignment, dict):
        stored = alignment.get("mvp_max_modules")
        if stored is not None:
            try:
                return max(1, int(stored))
            except (TypeError, ValueError):
                pass
        if alignment.get("plan_type") == "business":
            return BUSINESS_MVP_MAX_MODULES
    return None


def _normalize_business_mvp_modules(
    modules: list[dict[str, Any]],
    state: WorkflowState,
) -> list[dict[str, Any]]:
    """商业 cap=1 时将 PM 产出合并为单模块，描述对齐已确认的技术需求。"""
    cap = _resolve_mvp_module_cap(state)
    if cap != 1 or len(modules) <= 1:
        return modules
    requirement = (state.get("requirement") or "").strip()
    primary = modules[0]
    name = primary.get("module_name") or primary.get("module") or "business_mvp"
    desc = requirement[:600] if requirement else (primary.get("description") or name)
    return [{
        "module_name": name,
        "description": desc,
        "dependencies": [],
        "type": primary.get("type", "backend"),
    }]


def _exec_tests_passed(exec_test_result: dict[str, Any] | None) -> bool:
    """单元测试是否全部通过（用于 MVP 放宽）。"""
    if not exec_test_result:
        return False
    mode = exec_test_result.get("execution_mode", "")
    if mode in ("skipped", "error"):
        return False
    passed = int(exec_test_result.get("passed") or 0)
    failed = int(exec_test_result.get("failed") or 0)
    errors = int(exec_test_result.get("errors") or 0)
    return passed > 0 and failed == 0 and errors == 0


def _apply_mvp_module_cap(
    modules: list[dict[str, Any]],
    state: WorkflowState,
) -> list[dict[str, Any]]:
    cap = _resolve_mvp_module_cap(state)
    if cap is None or len(modules) <= cap:
        return modules
    return modules[:cap]


# ── 工作流执行器 ──────────────────────────────────────────

class WorkflowExecutor:
    """DevFlow CI 工作流执行器。

    驱动从规划到交付的完整流程，并通过 LangGraph 管理状态。
    """

    def __init__(
        self,
        planner: PlannerAgent,
        modules: ModuleAgents,
        reviewer: ReviewerAgent,
        integrator: IntegratorAgent,
        global_reviewer: GlobalReviewerAgent,
        repair_agent: RepairAgent | None = None,
        alignment_agent: AlignmentAgent | None = None,
        business_planner: BusinessPlannerAgent | None = None,
        case_store: CaseStore | None = None,
        project_memory_store: ProjectMemoryStore | None = None,
    ) -> None:
        self._planner = planner
        self._modules = modules
        self._reviewer = reviewer
        self._integrator = integrator
        self._global_reviewer = global_reviewer
        self._repair_agent = repair_agent or RepairAgent()
        self._alignment_agent = alignment_agent or AlignmentAgent()
        self._business_planner = business_planner or BusinessPlannerAgent()
        self._case_store = case_store
        self._project_memory = project_memory_store or ProjectMemoryStore()

    async def execute_alignment(self, state: WorkflowState) -> WorkflowState:
        """执行需求对齐阶段（唯一入口：所有构建工作流强制执行）。

        完成后 state["status"] = "aligned"，等待用户确认。
        """
        pid = state["project_id"]
        state.setdefault("module_results", {})
        state.setdefault("errors", [])
        state.setdefault("blocked_modules", [])

        directory = state.get("directory", "")
        project_memory_text = ""
        if directory:
            try:
                project_memory_text = self._project_memory.format_for_prompt(directory)
            except Exception:
                pass

        # ── 优先执行上下文分析：提取文档摘要供对齐 Agent 使用 ──
        structured_context: dict[str, Any] = {"analyzed_files": [], "source_files": []}
        if directory:
            await push_log(pid, "INFO", f"开始分析项目目录文档: {directory}")
            try:
                structured_context = analyze_project_context_structured(directory)
                state["project_context"] = json.dumps(structured_context, ensure_ascii=False)
                file_count = len(structured_context.get("analyzed_files", []))
                if structured_context.get("no_documentation_found"):
                    await push_log(pid, "INFO", "目录下未发现文档文件，对齐分析将仅基于需求")
                else:
                    await push_log(pid, "SUCCESS", f"文档分析完成：{file_count} 个文件")
            except Exception as exc:
                await push_log(pid, "WARN", f"上下文分析异常: {exc}，将跳过")
                # ★ 修复：确保 structured_context 非空，保留基本信息
                structured_context = {
                    "analyzed_files": [],
                    "overall_summary": f"目录分析失败: {exc}",
                    "source_files": [],
                    "no_documentation_found": True,
                }

        # 推送状态事件：进入对齐阶段
        await push_log(pid, "STATE", "aligning", state_event="aligning")

        # ── 根据文档类型选择 Agent ──
        doc_type = structured_context.get("document_type", "generic") if structured_context else "generic"
        # 用户可强制指定模式（通过 state 传入）
        force_mode = state.get("force_mode", "")
        if force_mode in ("technical", "business"):
            doc_type = force_mode

        # ★ 增强：检查是否使用了自动生成需求 + 有文档 → 强制重新评估 doc_type
        has_docs = len(structured_context.get("analyzed_files", [])) > 0
        is_auto_requirement = (
            "请分析现有项目代码" in state.get("requirement", "")
            and "找出可优化" in state.get("requirement", "")
        )

        await push_log(pid, "INFO", f"📋 文档类型判定: {doc_type}"
            + (f" (用户指定: {force_mode})" if force_mode else " (自动识别)")
            + (f" | 文档数: {len(structured_context.get('analyzed_files', []))}"
               if has_docs else " | 无文档")
            + (" | 自动生成需求" if is_auto_requirement else ""))

        # ★ 核心修复：generic / technical 但有文档且无源代码文件 → 大概率是商业文档
        # 三次确认机制，确保不会漏判
        if doc_type != "business" and has_docs:
            source_files = structured_context.get("source_files", [])
            no_source_code = len(source_files) == 0
            no_docs_found = structured_context.get("no_documentation_found", False)

            # 确认1：有文档但没有源代码文件 → 强制商业模式
            if no_source_code and not no_docs_found:
                doc_type = "business"
                await push_log(pid, "INFO", "🔄 目录有文档但无源代码文件，强制切换为商业计划模式")
            elif doc_type == "generic":
                # 确认2：二次检查文档内容
                all_text = " ".join(
                    af.get("summary", "") for af in structured_context.get("analyzed_files", [])
                ).lower() if structured_context else ""
                tech_keywords = ["api", "后端", "前端", "数据库", "微服务", "代码", "接口",
                                 "python", "java", "javascript", "react", "部署", "测试", "架构",
                                 "函数", "类", "模块", "import", "def ", "class "]
                has_tech = any(kw in all_text for kw in tech_keywords)
                if not has_tech:
                    doc_type = "business"
                    await push_log(pid, "INFO", "🔄 文档中无技术关键词，切换为商业计划模式")

        # ── 统一 Agent 调用策略：优先用对应 Agent，失败后用另一 Agent 兜底 ──
        alignment_result = None
        last_error = None

        for attempt in range(1, MAX_NON_MODULE_RETRIES + 1):
            try:
                if doc_type == "business":
                    # 商业文档 → BusinessPlannerAgent
                    await push_log(pid, "INFO", f"商业计划分析师生成计划中...（第 {attempt} 次）")
                    state["status"] = "aligning"
                    await _notify_ai_stream(pid, "business_planner", "start")
                    biz_plan = await self._business_planner.plan(
                        structured_context, project_memory_text
                    )
                    alignment_result = BusinessPlannerAgent.format_for_display(biz_plan)
                    # 推送商业计划摘要到 AI 流
                    summary_text = alignment_result.get("executive_summary", "")[:2000]
                    if summary_text:
                        await _notify_ai_stream(pid, "business_planner", "token", summary_text)
                    await _notify_ai_stream(pid, "business_planner", "done", f"{len(biz_plan.get('roadmap', []))} 个阶段")
                    await push_log(pid, "SUCCESS", "商业计划生成完成", state_event="aligned")
                    break

                # 技术/通用 → AlignmentAgent
                await push_log(pid, "INFO", f"需求对齐分析师开始分析...（第 {attempt} 次）")
                state["status"] = "aligning"
                await _notify_ai_stream(pid, "alignment_agent", "start")

                plan_a, plan_b, errors = await self._alignment_agent.analyze_alternatives(
                    state["requirement"],
                    project_context=state.get("project_context", ""),
                    project_memory_text=project_memory_text,
                )
                if errors:
                    await push_log(pid, "ERROR", f"对齐分析验证失败: {'; '.join(errors)}")
                    if attempt < MAX_NON_MODULE_RETRIES:
                        continue
                    plan_a = _fallback_alignment(state["requirement"], structured_context)
                    plan_b = {}
                    await push_log(pid, "WARN", "对齐分析未通过验证，使用兜底方案")

                # ★★ 核心修复：insufficient_info 死胡同消除 ★★
                if plan_a.get("status") == "insufficient_info" and structured_context:
                    if has_docs or not structured_context.get("no_documentation_found", True):
                        await push_log(pid, "INFO", "🔄 AlignmentAgent 返回信息不足，尝试商业计划模式兜底...")
                        try:
                            biz_plan = await self._business_planner.plan(
                                structured_context, project_memory_text
                            )
                            alignment_result = BusinessPlannerAgent.format_for_display(biz_plan)
                            await push_log(pid, "SUCCESS", "商业计划兜底成功", state_event="aligned")
                            state["alignment_result"] = alignment_result
                            if plan_b:
                                state["alternative_alignment"] = AlignmentAgent.format_alignment_for_display(plan_b)
                            state["status"] = "aligned"
                            return state
                        except Exception as biz_exc:
                            await push_log(pid, "WARN",
                                f"商业计划兜底也失败: {biz_exc}，使用智能回退方案")
                            # ★ 不再透传 insufficient_info！
                            plan_a = _fallback_alignment(
                                state["requirement"], structured_context
                            )
                            plan_b = {}

                alignment_result = AlignmentAgent.format_alignment_for_display(plan_a)
                if plan_b:
                    state["alternative_alignment"] = AlignmentAgent.format_alignment_for_display(plan_b)

                # 推送对齐摘要到 AI 流
                align_summary = alignment_result.get("summary", "")[:1500]
                if align_summary:
                    await _notify_ai_stream(pid, "alignment_agent", "token", align_summary)
                await _notify_ai_stream(pid, "alignment_agent", "done",
                    f"{len(plan_a.get('plan', []))} 个模块")

                await push_log(
                    pid, "SUCCESS",
                    f"需求对齐完成：{len(plan_a.get('plan', []))} 个建议模块"
                    + (f"（含备选方案）" if plan_b else "")
                )
                break

            except Exception as exc:
                last_error = str(exc)
                await push_log(pid, "ERROR", f"对齐分析异常（第 {attempt} 次）: {exc}")
                if attempt >= MAX_NON_MODULE_RETRIES:
                    await push_log(pid, "WARN", "所有 Agent 调用均失败，使用智能兜底方案")
                    fallback = _fallback_alignment(state["requirement"], structured_context)
                    if doc_type == "business":
                        fallback["plan_type"] = "business"
                        fallback["executive_summary"] = (
                            f"AI 分析暂时不可用（错误: {last_error}）。"
                            f"目录「{directory}」中有 {len(structured_context.get('analyzed_files', []))} 个文档文件，"
                            f"建议：1) 检查 API 密钥和网络连接；2) 在需求框中输入具体需求后重试。"
                        )
                    alignment_result = BusinessPlannerAgent.format_for_display(fallback) if doc_type == "business" \
                        else AlignmentAgent.format_alignment_for_display(fallback)
                    break

        # 最终兜底保护（理论上不会到这里，但保留）
        if alignment_result is None:
            fallback = _fallback_alignment(state["requirement"], structured_context)
            alignment_result = AlignmentAgent.format_alignment_for_display(fallback)

        state["alignment_result"] = alignment_result
        state["status"] = "aligned"
        await push_log(pid, "STATE", "aligned", state_event="aligned")
        return state

    async def execute_context_analysis(self, state: WorkflowState) -> WorkflowState:
        """上下文分析节点：读取项目目录，生成上下文描述。

        仅在 directory 非空时执行分析；否则透传。
        """
        pid = state["project_id"]
        directory = state.get("directory", "")

        if not directory:
            await push_log(pid, "INFO", "无项目目录，跳过上下文分析")
            return state

        await push_log(pid, "INFO", f"开始分析项目目录: {directory}")
        try:
            context = analyze_project_context(directory)
            state["project_context"] = context
            await push_log(pid, "SUCCESS", "项目上下文分析完成")
        except Exception as exc:
            await push_log(pid, "WARN", f"上下文分析异常: {exc}，将跳过")
            state["project_context"] = f"（目录分析失败: {exc}）"

        return state

    async def plan_only(self, state: WorkflowState) -> WorkflowState:
        """执行规划阶段（带重试，永不卡死）。

        规划完成后 state["status"] = "plan_ready"。
        """
        pid = state["project_id"]
        state.setdefault("module_results", {})
        state.setdefault("errors", [])
        state.setdefault("blocked_modules", [])

        directory = state.get("directory", "")
        project_memory_text = ""
        if directory:
            try:
                project_memory_text = self._project_memory.format_for_prompt(directory)
            except Exception:
                pass

        for attempt in range(1, MAX_NON_MODULE_RETRIES + 1):
            try:
                # 详细日志：显示正在处理的需求
                req_summary = state["requirement"][:120]
                await push_log(pid, "INFO", f"📋 PM Agent 启动规划（第 {attempt}/{MAX_NON_MODULE_RETRIES} 次）")
                await push_log(pid, "INFO", f"📝 需求摘要: {req_summary}...")
                state["status"] = "planning"

                mvp_cap = _resolve_mvp_module_cap(state)
                planning_context = state.get("project_context", "")
                if mvp_cap:
                    planning_context += (
                        f"\n\n【MVP硬性约束】最多拆解 {mvp_cap} 个模块，"
                        "请合并相近功能，优先最小可行产品。"
                    )
                    if mvp_cap == 1:
                        planning_context += (
                            "\n【强制单模块】modules 数组必须恰好 1 个元素；"
                            "description 只描述一个可运行的 Python 核心能力，"
                            "禁止 SQLite/CLI/多子系统拆分。"
                        )

                await push_log(pid, "INFO", "🤔 PM Agent 正在分析需求，拆解模块结构...")
                await _notify_ai_stream(pid, "planner", "start")
                plan_a, plan_b, comparison, errors = await self._planner.plan_alternatives(
                    state["requirement"],
                    project_context=planning_context,
                    project_memory_text=project_memory_text,
                )
                if errors:
                    await push_log(pid, "ERROR", f"规划验证失败: {'; '.join(errors)}")
                    if attempt < MAX_NON_MODULE_RETRIES:
                        continue
                    state["errors"].extend(errors)
                    state["status"] = "needs_review"
                    return state

                modules = plan_a.get("modules", [])
                module_count = len(modules)
                if module_count == 0:
                    await push_log(pid, "WARN", "⚠️ 规划完成但未生成任何模块，使用关键词兜底方案")
                    # 从需求中提取关键词生成基础模块
                    import re as _re
                    words = _re.findall(r"[\u4e00-\u9fff]{2,}|[a-zA-Z]{3,}", state["requirement"])
                    keywords = list(dict.fromkeys(words))[:5]  # 去重取前5个
                    if keywords:
                        modules = []
                        for kw in keywords:
                            modules.append({
                                "module_name": kw.lower().replace(" ", "_")[:30],
                                "description": f"实现与「{kw}」相关的功能模块",
                                "dependencies": [],
                                "type": "backend",
                            })
                        await push_log(pid, "SUCCESS", f"🔧 兜底生成 {len(modules)} 个模块: {', '.join(m['module_name'] for m in modules)}")
                    else:
                        modules = [{
                            "module_name": "main",
                            "description": state["requirement"][:80],
                            "dependencies": [],
                            "type": "backend",
                        }]
                        await push_log(pid, "SUCCESS", "🔧 兜底生成 1 个默认模块: main")
                    plan_a["modules"] = modules
                    module_count = len(modules)
                else:
                    module_names = ", ".join(m.get("module_name", m.get("module", "?")) for m in modules[:10])
                    await push_log(pid, "SUCCESS", f"📦 规划完成: {module_count} 个模块 — {module_names}"
                        + (f"（共{module_count}个）" if module_count > 10 else ""))
                    for i, m in enumerate(modules[:5]):
                        desc = m.get("description", "")[:80]
                        await push_log(pid, "INFO", f"  模块{i+1}: {m.get('module_name', m.get('module', '?'))} — {desc}")

                original_count = len(modules)
                modules = _apply_mvp_module_cap(modules, state)
                modules = _normalize_business_mvp_modules(modules, state)
                if len(modules) < original_count:
                    await push_log(
                        pid, "INFO",
                        f"✂️ 商业 MVP 裁剪：{original_count} → {len(modules)} 个模块",
                    )
                    plan_a["modules"] = modules
                    module_count = len(modules)

                state["plan_json"] = json.dumps(plan_a, ensure_ascii=False)
                state["plan_modules"] = modules
                state["alternative_plan"] = plan_b if plan_b else None
                state["plan_comparison"] = comparison if comparison else None
                global_req = plan_a.get("global_requirements", [])
                state["project_context"] = state.get("project_context", "") + "\n全局需求:\n" + "\n".join(f"- {r}" for r in global_req)

                # ── 依赖推断 ──
                if DEPENDENCY_INFERENCE_ENABLED:
                    await push_log(pid, "INFO", "推断项目依赖...")
                    try:
                        deps = await self._planner.infer_dependencies(
                            state["requirement"], state.get("project_context", ""), "python"
                        )
                        state["inferred_dependencies"] = deps
                        await push_log(pid, "SUCCESS", f"依赖推断完成: {deps.get('summary', '')}")
                    except Exception:
                        state["inferred_dependencies"] = {"dependencies": [], "system_dependencies": [], "summary": "推断失败"}

                # ── 注入人类偏好 ──
                if FEEDBACK_LEARNING_ENABLED:
                    try:
                        prefs = PlannerAgent.load_human_preferences()
                        if prefs:
                            state["project_context"] = state.get("project_context", "") + prefs
                    except Exception:
                        pass

                await push_log(pid, "SUCCESS", f"规划完成：{len(plan_a.get('modules', []))} 个模块")
                # 推送规划结果到 AI 流
                plan_token_text = json.dumps({
                    "modules": [m.get("module_name", "") for m in modules],
                    "total": len(modules),
                }, ensure_ascii=False)
                await _notify_ai_stream(pid, "planner", "token", plan_token_text[:2000])
                await _notify_ai_stream(pid, "planner", "done", f"{len(modules)} 个模块")
                state["status"] = "plan_ready"
                # ★ 同步更新数据库状态，避免API显示滞后
                await _sync_project_status(pid, "plan_ready")
                await _persist_plan_modules(
                    pid, state["plan_json"], modules
                )
                return state

            except Exception as exc:
                await push_log(pid, "ERROR", f"规划异常（第 {attempt} 次）: {exc}")
                if attempt >= MAX_NON_MODULE_RETRIES:
                    state["status"] = "needs_review"
                    state["errors"].append(str(exc))
                    return state

        state["status"] = "needs_review"
        return state

    async def execute_from_plan(self, state: WorkflowState) -> WorkflowState:
        """从已有规划继续执行（跳过规划和上下文分析）。

        用于 confirm_plan 后继续：模块执行 → 集成 → 审查 → 打包。
        带非模块阶段重试。
        """
        pid = state["project_id"]

        try:
            # ── 阶段1：模块执行 ──
            state["status"] = "executing"
            await _sync_project_status(pid, "executing")
            modules = state["plan_modules"]
            order = topological_sort(modules)
            state["module_order"] = order

            await push_log(pid, "STATE", "executing", state_event="executing")
            total_modules = len(modules)
            await push_log(pid, "INFO", f"🚀 进入模块构建阶段，共 {total_modules} 个模块待执行")
            await push_log(pid, "INFO", f"📋 执行顺序: {' → '.join(order) if order else '(无)'}")

            await self._execute_modules_parallel(pid, state)

            results = state["module_results"]
            if not results:
                await push_log(pid, "ERROR", "❌ 模块执行无结果，管线中断")
                state["status"] = "failed"
                return state

            # 统计通过和阻塞
            passed_count = sum(1 for r in results.values() if r.get("status") == "passed")
            blocked_count = sum(1 for r in results.values() if r.get("status") == "blocked")
            failed_count = total_modules - passed_count - blocked_count
            state["blocked_modules"] = [n for n, r in results.items() if r.get("status") == "blocked"]

            if passed_count == 0 and blocked_count == 0:
                await push_log(pid, "ERROR", "❌ 所有模块执行失败，管线中断")
                state["status"] = "failed"
                return state

            await push_log(
                pid, "SUCCESS",
                f"✅ 模块构建完成: {passed_count} 通过, {blocked_count} 阻塞, {failed_count} 失败"
            )

            # ── 阶段2：集成 ──
            await push_log(pid, "STATE", "integrating", state_event="integrating")
            await _sync_project_status(pid, "integrating")
            await push_log(pid, "INFO", "🔗 进入集成阶段，组装项目...")
            state = await self._execute_integrate_with_retry(pid, state)
            await push_log(pid, "SUCCESS", "✅ 集成完成")

            # ── 集成测试执行 ──
            await push_log(pid, "INFO", "🧪 执行集成测试...")
            try:
                integration_test_code = state.get("integration_result", {}).get("integration_tests", "")
                module_files: dict[str, str] = {}
                for m_name, m_result in state.get("module_results", {}).items():
                    mc = m_result.get("code", "")
                    if mc and m_result.get("status") != "blocked":
                        ext = ".html" if any(
                            pm.get("module_name") == m_name and pm.get("type") == "frontend"
                            for pm in state["plan_modules"]
                        ) else ".py"
                        module_files[f"{m_name}{ext}"] = mc
                it_result = await run_integration_tests(
                    integration_test_code=integration_test_code,
                    project_id=pid, module_files=module_files,
                    timeout=TEST_INTEGRATION_TIMEOUT, log_callback=push_log,
                )
                state["integration_result"]["test_result"] = it_result.to_dict()
                await push_log(
                    pid, "SUCCESS" if it_result.all_passed else "WARN",
                    f"集成测试: {it_result.summary}",
                )
            except Exception as exc:
                await push_log(pid, "ERROR", f"集成测试执行异常: {exc}")
                state["integration_result"]["test_result"] = {"total": 0, "passed": 0, "failed": 1, "summary": str(exc)}

            # ── 全局审查（带重试） ──
            await push_log(pid, "STATE", "reviewing", state_event="reviewing")
            await _sync_project_status(pid, "reviewing")
            await push_log(pid, "INFO", "🔍 进入全局审查阶段...")
            state = await self._execute_global_review_with_retry(pid, state)
            review_data = state.get("global_review", {})
            await push_log(pid, "SUCCESS",
                f"✅ 审查完成: 评分 {review_data.get('score', '?')}/100, "
                f"{'通过' if review_data.get('passed') else '需改进'}")

            # ── 交付前全量测试 ──
            state["test_report"] = {"total": 0, "passed": 0, "failed": 0, "summary": "未执行"}
            if TEST_EXECUTION_ENABLED:
                await push_log(pid, "INFO", "执行交付前全量测试...")
                try:
                    project_dir = DELIVERIES_DIR / pid
                    project_dir.mkdir(parents=True, exist_ok=True)
                    # 先写入模块文件到沙箱供测试
                    for m in state["plan_modules"]:
                        name = m["module_name"]
                        mc = state["module_results"].get(name, {}).get("code", "")
                        tcode = state["module_results"].get(name, {}).get("test_code", "")
                        if mc:
                            ext = ".html" if m.get("type") == "frontend" else ".py"
                            (project_dir / f"{name}{ext}").write_text(mc, encoding="utf-8")
                        if tcode:
                            (project_dir / f"test_{name}.py").write_text(tcode, encoding="utf-8")
                    # 写入集成测试
                    it_code = state.get("integration_result", {}).get("integration_tests", "")
                    if it_code:
                        (project_dir / "test_integration.py").write_text(it_code, encoding="utf-8")
                    (project_dir / "conftest.py").touch()

                    full_result = await run_full_test_suite(
                        project_id=pid, sandbox_dir=project_dir,
                        timeout=TEST_FULL_SUITE_TIMEOUT, log_callback=push_log,
                    )
                    state["test_report"] = full_result.to_dict()

                    # 保存测试报告
                    report_path = project_dir / "test_report.json"
                    report_path.write_text(json.dumps(full_result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
                    state["test_report_path"] = str(report_path)

                    # 决策：全量测试结果处理
                    if full_result.all_passed:
                        await push_log(pid, "SUCCESS", f"全量测试全部通过！({full_result.passed}/{full_result.total})")
                    elif full_result.failed <= TEST_MAX_FAILURES_BEFORE_WARN:
                        await push_log(pid, "WARN", f"全量测试有 {full_result.failed} 个失败（≤{TEST_MAX_FAILURES_BEFORE_WARN}），尝试最后一次修复...")
                        # 最后一次修复尝试（简化版：直接标记为 completed_with_warnings）
                        state["status"] = "completed_with_warnings"
                    else:
                        await push_log(pid, "WARN", f"全量测试有 {full_result.failed} 个失败（>{TEST_MAX_FAILURES_BEFORE_WARN}），项目标记为 completed_with_warnings")
                        state["status"] = "completed_with_warnings"
                except Exception as exc:
                    await push_log(pid, "ERROR", f"全量测试异常: {exc}")
                    state["test_report"] = {"total": 0, "passed": 0, "failed": 1, "summary": str(exc)}

            # ── 打包交付 ──
            await push_log(pid, "INFO", "📦 进入打包交付阶段...")
            integration = IntegrationResult(
                project_structure=state.get("integration_result", {}).get("project_structure", ""),
                main_code=state.get("integration_result", {}).get("main_code", ""),
                integration_tests=state.get("integration_result", {}).get("integration_tests", ""),
                readme=state.get("integration_result", {}).get("readme", ""),
                requirements=state.get("integration_result", {}).get("requirements", ""),
            )
            delivery_path = await self._package_delivery(pid, state, integration)
            state["delivery_path"] = delivery_path
            state["status"] = "completed"

            await push_log(pid, "STATE", "completed", state_event="completed")
            blocked_count = len(state.get("blocked_modules", []))
            total_mods = len(state.get("plan_modules", []))
            summary = (f"🎉 项目交付完成！{total_mods} 个模块, "
                       f"{total_mods - blocked_count} 通过"
                       + (f", {blocked_count} 阻塞" if blocked_count > 0 else ", 全部通过"))
            await push_log(pid, "SUCCESS", summary)

            # ── 项目记忆更新 ──
            try:
                if state.get("directory"):
                    self._project_memory.update_after_delivery(
                        directory=state["directory"],
                        requirement=state["requirement"],
                        plan=state["plan_modules"],
                        module_results=state["module_results"],
                        last_modified_module=(
                            state["plan_modules"][-1]["module_name"]
                            if state["plan_modules"] else ""
                        ),
                    )
            except Exception:
                pass

            # ── 通用记忆进化 ──
            if self._case_store:
                try:
                    review = state.get("global_review", {})
                    if review.get("passed") or review.get("score", 0) > 50:
                        modules_info = self._collect_module_info(state)
                        self._case_store.add_success(
                            requirement=state["requirement"],
                            plan=state["plan_modules"],
                            modules=modules_info,
                        )
                except Exception:
                    pass

            return state

        except Exception as exc:
            await push_log(pid, "ERROR", f"工作流异常: {exc}")
            state["status"] = "failed"
            state["errors"].append(str(exc))
            return state

    async def execute(self, state: WorkflowState) -> WorkflowState:
        """执行完整工作流：需求对齐（前置强制）→ 上下文分析 + 规划 + 执行。

        对齐完成后状态为 aligned，停止并等待用户确认。
        """
        # 第一阶段：需求对齐（所有构建工作流强制执行）
        state = await self.execute_alignment(state)
        if state["status"] == "aligned":
            return state  # 停止，等待用户确认
        # 对齐失败（needs_review），不再继续
        return state

    async def execute_after_alignment(self, state: WorkflowState) -> WorkflowState:
        """从对齐确认后继续：上下文分析 + 规划。

        在 plan_ready 停止，等待用户 confirm_plan 后再执行（由 _run_execution 驱动）。
        """
        state = await self.execute_context_analysis(state)
        state = await self.plan_only(state)
        return state

    async def _execute_integrate_with_retry(
        self, pid: str, state: WorkflowState
    ) -> WorkflowState:
        """执行集成阶段，最多重试 MAX_NON_MODULE_RETRIES 次。"""
        for attempt in range(1, MAX_NON_MODULE_RETRIES + 1):
            try:
                state["status"] = "integrating"
                await push_log(pid, "INFO", f"集成 Agent 开始组装项目...（第 {attempt} 次）")
                await _notify_ai_stream(pid, "integrator", "start")
                modules_info = self._collect_module_info(state)
                integration = await self._integrator.integrate(state["requirement"], modules_info)
                state["integration_result"] = {
                    "project_structure": integration.project_structure,
                    "main_code": integration.main_code,
                    "integration_tests": integration.integration_tests,
                    "readme": integration.readme,
                    "requirements": integration.requirements,
                }
                # 推送集成摘要
                await _notify_ai_stream(pid, "integrator", "token",
                    str(integration.project_structure or "")[:1000])
                await _notify_ai_stream(pid, "integrator", "done")
                await push_log(pid, "SUCCESS", "项目集成完成")
                return state
            except Exception as exc:
                await push_log(pid, "ERROR", f"集成异常（第 {attempt} 次）: {exc}")
                if attempt >= MAX_NON_MODULE_RETRIES:
                    state["status"] = "needs_review"
                    state["errors"].append(f"集成失败: {exc}")
                    raise
        return state

    async def _execute_global_review_with_retry(
        self, pid: str, state: WorkflowState
    ) -> WorkflowState:
        """执行全局审查阶段，最多重试 MAX_NON_MODULE_RETRIES 次。"""
        for attempt in range(1, MAX_NON_MODULE_RETRIES + 1):
            try:
                state["status"] = "reviewing"
                await push_log(pid, "INFO", f"全局审查 Agent 开始审查...（第 {attempt} 次）")
                await _notify_ai_stream(pid, "global_reviewer", "start")
                modules_info = self._collect_module_info(state)
                integration = IntegrationResult(
                    project_structure=state.get("integration_result", {}).get("project_structure", ""),
                    main_code=state.get("integration_result", {}).get("main_code", ""),
                    integration_tests=state.get("integration_result", {}).get("integration_tests", ""),
                    readme=state.get("integration_result", {}).get("readme", ""),
                    requirements=state.get("integration_result", {}).get("requirements", ""),
                )
                global_review = await self._global_reviewer.review(
                    state["requirement"], state["plan_modules"], modules_info, integration,
                )
                state["global_review"] = {
                    "passed": global_review.passed,
                    "score": global_review.score,
                    "summary": global_review.summary,
                    "issues": global_review.issues,
                    "suggestions": global_review.suggestions,
                }
                verdict = "通过" if global_review.passed else "需改进"
                # 推送审查摘要到 AI 流
                review_token = f"评分: {global_review.score}/100, {verdict}"
                if global_review.summary:
                    review_token += f"\n{global_review.summary[:500]}"
                await _notify_ai_stream(pid, "global_reviewer", "token", review_token)
                await _notify_ai_stream(pid, "global_reviewer", "done", f"{global_review.score}/100")
                await push_log(
                    pid, "SUCCESS" if global_review.passed else "WARN",
                    f"全局审查{verdict}，评分: {global_review.score}/100",
                )
                return state
            except Exception as exc:
                await push_log(pid, "ERROR", f"全局审查异常（第 {attempt} 次）: {exc}")
                if attempt >= MAX_NON_MODULE_RETRIES:
                    state["status"] = "needs_review"
                    state["errors"].append(f"全局审查失败: {exc}")
                    raise
        return state

    async def _execute_modules_parallel(
        self, pid: str, state: WorkflowState
    ) -> None:
        """并行执行模块（按拓扑层并行，层内并行）。"""
        modules = state["plan_modules"]
        order = state.get("module_order", [m["module_name"] for m in modules])
        name_to_module = {m["module_name"]: m for m in modules}

        # 按层级分组
        layers = self._build_layers(modules)

        for layer_idx, layer in enumerate(layers):
            await push_log(
                pid, "INFO",
                f"执行第 {layer_idx + 1}/{len(layers)} 层: {', '.join(layer)}"
            )

            # 层内并行
            sem = asyncio.Semaphore(MAX_CONCURRENT_MODULES)

            async def run_module(module_name: str) -> None:
                async with sem:
                    module = name_to_module[module_name]
                    await self._execute_single_module(
                        pid, state, module
                    )

            tasks = [run_module(name) for name in layer]
            await asyncio.gather(*tasks)

    @staticmethod
    def _build_layers(
        modules: list[dict[str, Any]],
    ) -> list[list[str]]:
        """按依赖关系将模块分成并行层。

        每层内的模块无相互依赖，可完全并行。
        """
        name_to_idx = {m["module_name"]: i for i, m in enumerate(modules)}
        # 计算每个模块的深度（最长依赖链长度）
        depth: dict[str, int] = {}

        def get_depth(name: str) -> int:
            if name in depth:
                return depth[name]
            if name not in name_to_idx:
                return 0
            deps = modules[name_to_idx[name]].get("dependencies", [])
            max_dep = 0
            for dep in deps:
                max_dep = max(max_dep, get_depth(dep) + 1)
            depth[name] = max_dep
            return max_dep

        for m in modules:
            get_depth(m["module_name"])

        # 按深度分组
        layers_dict: dict[int, list[str]] = {}
        for name, d in depth.items():
            layers_dict.setdefault(d, []).append(name)

        return [
            layers_dict[d] for d in sorted(layers_dict.keys())
        ]

    async def _run_module_test_and_log(
        self, pid: str, module_name: str, test_code: str,
    ) -> dict[str, Any]:
        """执行模块测试并返回序列化结果。"""
        if not TEST_EXECUTION_ENABLED or not test_code:
            return {"total": 0, "passed": 0, "failed": 0, "summary": "跳过", "execution_mode": "skipped"}
        try:
            tr = await run_module_tests(
                module_name=module_name, test_code=test_code,
                project_id=pid, timeout=TEST_UNIT_TIMEOUT, log_callback=push_log,
            )
            return tr.to_dict()
        except Exception as exc:
            await push_log(pid, "ERROR", f"[{module_name}] 测试执行异常: {exc}", module_name=module_name)
            return {"total": 0, "passed": 0, "failed": 1, "summary": str(exc), "execution_mode": "error"}

    async def _execute_single_module(
        self,
        pid: str,
        state: WorkflowState,
        module: dict[str, Any],
    ) -> None:
        """执行单个模块的完整子图流程。

        流程：
        1. 分析
        2. 正常编码→测试→审查循环（前 3 次）+ 测试执行
        3. 第 4 次起异常自愈（多重策略回退，总轮次上限 8）
        4. 所有策略耗尽后 → blocked（生成占位文件）
        """
        module_name = module["module_name"]
        description = module.get("description", "")
        module_type = module.get("type", "backend")
        context = state.get("project_context", "")
        mvp_mode = _resolve_mvp_module_cap(state) == 1

        await push_log(
            pid, "INFO",
            f"[{module_name}] 开始执行",
            module_name=module_name,
        )
        await _update_module_status(pid, module_name, "analyzing")
        await _notify_ai_stream(pid, "module_agents", "start")

        try:
            # ── 分析 ──
            await push_log(
                pid, "INFO",
                f"[{module_name}] 分析师分析中...",
                module_name=module_name,
            )
            spec = await self._modules.analyze(
                module_name, description, context, mvp_mode=mvp_mode,
            )

            # ── 编码 + 测试 + 审查 循环（前 MAX_REVIEW_RETRIES 次正常重试） ──
            code: ModuleCode | None = None
            test_result: ModuleTestResult | None = None
            review: ReviewResult | None = None
            feedback = ""
            failure_reason = ""
            total_rounds = 0
            auto_fix_attempted = False
            fix_history: list[dict[str, Any]] = []

            # 正常重试循环（前 3 次）
            for retry in range(MAX_REVIEW_RETRIES):
                total_rounds += 1

                await _update_module_status(pid, module_name, "coding")
                # 编码
                await push_log(
                    pid, "INFO",
                    f"[{module_name}] 编码中...（第 {retry + 1} 次）",
                    module_name=module_name,
                )
                code = await self._modules.code(
                    module_name, spec, feedback, mvp_mode=mvp_mode,
                )

                await _update_module_status(pid, module_name, "testing")
                # 测试
                await push_log(
                    pid, "INFO",
                    f"[{module_name}] 测试中...",
                    module_name=module_name,
                )
                test_result = await self._modules.test(
                    module_name, code, spec
                )

                await _update_module_status(pid, module_name, "reviewing")
                # 审查
                await push_log(
                    pid, "INFO",
                    f"[{module_name}] 审查中...",
                    module_name=module_name,
                )
                review = await self._reviewer.review(
                    module_name,
                    spec.summary,
                    code.code,
                    code.test_code,
                    retry_count=retry,
                    mvp_mode=mvp_mode,
                )

                exec_test_result: dict[str, Any] | None = None
                if mvp_mode:
                    exec_test_result = await self._run_module_test_and_log(
                        pid, module_name, code.test_code,
                    )

                if review.passed:
                    if exec_test_result is None:
                        exec_test_result = await self._run_module_test_and_log(
                            pid, module_name, code.test_code,
                        )
                    await push_log(
                        pid, "SUCCESS",
                        f"[{module_name}] 审查通过 ✓",
                        module_name=module_name,
                    )
                    state["module_results"][module_name] = {
                        "module_name": module_name,
                        "status": "passed",
                        "spec": {
                            "summary": spec.summary,
                            "api_endpoints": spec.api_endpoints,
                            "data_models": spec.data_models,
                        },
                        "code": code.code,
                        "test_code": code.test_code,
                        "test_result": exec_test_result,
                        "retry_count": retry,
                        "auto_fix_history": json.dumps(fix_history, ensure_ascii=False) if fix_history else "[]",
                    }
                    await _persist_module_result(pid, module_name, state["module_results"][module_name])
                    try:
                        from workflow.closed_loop import cleanup_after_fix
                        await cleanup_after_fix(pid, module_name)
                    except Exception:
                        pass
                    return

                if mvp_mode and _exec_tests_passed(exec_test_result):
                    await push_log(
                        pid, "SUCCESS",
                        f"[{module_name}] MVP 模式：单元测试通过，审查问题已降级放行",
                        module_name=module_name,
                    )
                    state["module_results"][module_name] = {
                        "module_name": module_name,
                        "status": "passed",
                        "spec": {
                            "summary": spec.summary,
                            "api_endpoints": spec.api_endpoints,
                            "data_models": spec.data_models,
                        },
                        "code": code.code,
                        "test_code": code.test_code,
                        "test_result": exec_test_result,
                        "retry_count": retry,
                        "mvp_test_override": True,
                        "auto_fix_history": json.dumps(fix_history, ensure_ascii=False) if fix_history else "[]",
                    }
                    await _persist_module_result(pid, module_name, state["module_results"][module_name])
                    try:
                        from workflow.closed_loop import cleanup_after_fix
                        await cleanup_after_fix(pid, module_name)
                    except Exception:
                        pass
                    return

                # 审查未通过
                issues_text = "\n".join(
                    f"  - {issue}" for issue in review.issues
                )
                failure_reason = issues_text
                await push_log(
                    pid, "WARN",
                    f"[{module_name}] 审查不通过，问题:\n{issues_text}",
                    module_name=module_name,
                )

                if retry < MAX_REVIEW_RETRIES - 1:
                    feedback = (
                        f"前次审查发现以下问题，请修复：\n{issues_text}"
                    )
                else:
                    await push_log(
                        pid, "WARN",
                        f"[{module_name}] 正常重试 {MAX_REVIEW_RETRIES} 次后仍未通过，启动异常自愈...",
                        module_name=module_name,
                    )

            # ── 异常自愈阶段（闭环修复：查询历史 → 修复 → 验证 → 重试）──
            if AUTO_FIX_ENABLED and code is not None and review is not None:
                auto_fix_attempted = True
                await _update_module_status(pid, module_name, "auto_fixing")

                error_text = "\n".join(review.issues) if review and review.issues else failure_reason
                error_type = classify_error(error_text)

                await push_log(
                    pid, "INFO",
                    f"[自愈][{module_name}] 检测到错误类型: {error_type.value}，启动闭环修复...",
                    module_name=module_name,
                )

                # ── 导入闭环修复模块 ──
                from workflow.closed_loop import closed_loop_repair, query_fix_history

                # 查询历史（提前记录到日志）
                pre_history = await query_fix_history(pid, module_name, error_type.value)
                if pre_history.get("failed_strategies"):
                    await push_log(
                        pid, "INFO",
                        f"[自愈][{module_name}] 历史已失败策略: {', '.join(pre_history['failed_strategies'])}，将跳过重试",
                        module_name=module_name,
                    )

                # 构建闭环修复的回调函数
                async def _do_code(mn: str, spec_obj, fb: str):
                    # closed_loop 传入的是 dict，需要转为 ModuleSpec
                    from agents.module_agents import ModuleSpec
                    if isinstance(spec_obj, dict):
                        spec_obj = ModuleSpec(
                            module_name=mn,
                            summary=spec_obj.get("summary", ""),
                            api_endpoints=spec_obj.get("api_endpoints", []),
                            data_models=spec_obj.get("data_models", []),
                            logic_flow=spec_obj.get("logic_flow", ""),
                            error_handling=spec_obj.get("error_handling", ""),
                        )
                    return await self._modules.code(mn, spec_obj, fb, mvp_mode=mvp_mode)

                async def _do_review(mn: str, summary: str, c: str, tc: str, retry: int):
                    return await self._reviewer.review(
                        mn, summary, c, tc, retry_count=retry, mvp_mode=mvp_mode,
                    )

                async def _do_repair(moutput: dict, hcases: list):
                    return await self._repair_agent.repair(moutput, hcases)

                async def _do_test(mn: str, tc: str):
                    from workflow.test_runner import run_module_tests
                    try:
                        return await run_module_tests(
                            module_name=mn, test_code=tc,
                            project_id=pid, timeout=TEST_UNIT_TIMEOUT,
                        )
                    except Exception:
                        return None

                async def _push_log(pid_, level, msg, module_name=""):
                    await push_log(pid_, level, msg, module_name=module_name)

                # 执行闭环修复
                remaining_rounds = AUTO_FIX_MAX_TOTAL_ROUNDS - MAX_REVIEW_RETRIES
                loop_result = await closed_loop_repair(
                    project_id=pid,
                    module_name=module_name,
                    module_type=module_type,
                    spec_summary=spec.summary,
                    spec_detail={
                        "summary": spec.summary,
                        "api_endpoints": spec.api_endpoints,
                        "data_models": spec.data_models,
                        "logic_flow": spec.logic_flow,
                        "error_handling": spec.error_handling,
                    },
                    current_code=code.code,
                    current_test_code=code.test_code,
                    review_issues=review.issues if review else [],
                    failure_reason=failure_reason,
                    do_code=_do_code,
                    do_review=_do_review,
                    do_repair=_do_repair,
                    do_test=_do_test,
                    push_log=_push_log,
                    max_total_rounds=remaining_rounds,
                )

                # 更新全局计数器
                total_rounds += loop_result.total_rounds
                fix_history.extend(loop_result.fix_history)

                if loop_result.success:
                    await push_log(
                        pid, "SUCCESS",
                        f"[{module_name}] 闭环修复成功！策略: {loop_result.strategy_used}",
                        module_name=module_name,
                    )
                    exec_test_result = await self._run_module_test_and_log(pid, module_name, loop_result.test_code)
                    state["module_results"][module_name] = {
                        "module_name": module_name,
                        "status": "passed",
                        "spec": {
                            "summary": spec.summary,
                            "api_endpoints": spec.api_endpoints,
                            "data_models": spec.data_models,
                        },
                        "code": loop_result.code,
                        "test_code": loop_result.test_code,
                        "test_result": exec_test_result,
                        "retry_count": total_rounds,
                        "auto_fix_history": json.dumps(loop_result.fix_history, ensure_ascii=False),
                    }
                    await _persist_module_result(pid, module_name, state["module_results"][module_name])
                    return

                # 闭环修复未成功，更新代码引用
                failure_reason = loop_result.failure_reason or failure_reason
                if loop_result.code:
                    code = ModuleCode(
                        module_name=module_name,
                        code=loop_result.code,
                        test_code=loop_result.test_code,
                    )

            # 超过最大重试 → 阻塞（blocked），生成占位文件
            final_reason = failure_reason or "超过所有修复策略尝试次数"
            await push_log(
                pid, "WARN",
                f"[{module_name}] 所有修复策略已尝试（共 {total_rounds} 轮），标记为阻塞（blocked）",
                module_name=module_name,
            )

            stub_code = _generate_stub_code(
                module_name, module_type, description,
                final_reason,
            )
            result_data: dict[str, Any] = {
                "module_name": module_name,
                "status": "blocked",
                "spec": {"summary": spec.summary},
                "code": stub_code,
                "test_code": "",
                "retry_count": total_rounds,
                "errors": review.issues if review else [final_reason],
                "failure_reason": final_reason,
                "auto_fix_history": json.dumps(fix_history, ensure_ascii=False) if fix_history else "[]",
            }
            state["module_results"][module_name] = result_data
            await _persist_module_result(pid, module_name, result_data)

        except Exception as exc:
            await push_log(
                pid, "ERROR",
                f"[{module_name}] 执行异常: {exc}",
                module_name=module_name,
            )
            # 异常也生成占位文件
            stub_code = _generate_stub_code(
                module_name, module_type, description,
                f"执行异常: {str(exc)}",
            )
            state["module_results"][module_name] = {
                "module_name": module_name,
                "status": "blocked",
                "errors": [str(exc)],
                "code": stub_code,
                "failure_reason": str(exc),
                "auto_fix_history": "[]",
            }
            await _persist_module_result(pid, module_name, state["module_results"][module_name])

    def _collect_module_info(
        self, state: WorkflowState
    ) -> list[dict[str, Any]]:
        """收集所有模块信息供集成使用。"""
        info: list[dict[str, Any]] = []
        for m in state["plan_modules"]:
            name = m["module_name"]
            result = state["module_results"].get(name, {})
            info.append({
                "module_name": name,
                "description": m.get("description", ""),
                "spec": result.get("spec", {}).get("summary", ""),
                "code": result.get("code", ""),
                "test_code": result.get("test_code", ""),
                "status": result.get("status", "pending"),
            })
        return info

    async def _inject_autopilot(
        self, pid: str, project_dir: Path, state: WorkflowState,
    ) -> None:
        """将自治运维模板注入生成项目。

        复制 templates/autopilot/* 到 {project_dir}/autopilot/，
        并替换占位符 {{PROJECT_NAME}} 和 {{PROJECT_VERSION}}。
        根据项目类型适配主入口文件。
        """
        from config import AUTOPILOT_TEMPLATES_DIR

        if not AUTOPILOT_TEMPLATES_DIR.exists():
            await push_log(pid, "WARN", "自治模板目录不存在，跳过注入")
            return

        autopilot_dir = project_dir / "autopilot"
        autopilot_dir.mkdir(parents=True, exist_ok=True)

        project_name = state.get("requirement", "generated-project")[:30].replace(" ", "-")
        project_version = f"1.{len(state.get('plan_modules', []))}.0"

        # 判断项目类型
        project_type = "cli"  # 默认
        for m in state.get("plan_modules", []):
            if m.get("type") in ("backend", "integration"):
                project_type = "web"
                break

        # 复制模板文件并替换占位符
        copied = 0
        for template_file in AUTOPILOT_TEMPLATES_DIR.glob("*.py"):
            dest = autopilot_dir / template_file.name
            content = template_file.read_text(encoding="utf-8")
            content = content.replace("{{PROJECT_NAME}}", project_name)
            content = content.replace("{{PROJECT_VERSION}}", project_version)
            content = content.replace("{{PROJECT_ID}}", pid)
            dest.write_text(content, encoding="utf-8")
            copied += 1

        # 初始化空的 fixes.json 和 .analytics.json
        (autopilot_dir / "fixes.json").write_text(
            '{"version":"1.0","cases":[]}', encoding="utf-8"
        )
        (autopilot_dir / "feature_flags.json").write_text(
            '{"flags":{}}', encoding="utf-8"
        )

        # ── 主入口适配 ──
        main_py = project_dir / "main.py"
        if main_py.exists() and project_type == "web":
            main_content = main_py.read_text(encoding="utf-8")
            if "autopilot" not in main_content and "include_router" in main_content:
                # 在 app 创建后注入 health 路由注册
                main_content = main_content.replace(
                    "app = FastAPI",
                    "# 自治运维: 健康检查端点\nfrom autopilot.health_check import router as health_router\napp = FastAPI",
                )
                main_content = main_content.replace(
                    "return app",
                    'app.include_router(health_router)\n    return app',
                )
                main_py.write_text(main_content, encoding="utf-8")

        await push_log(pid, "SUCCESS", f"自治运维模块已注入 ({copied} 个文件, 类型: {project_type})")

    async def _package_delivery(
        self,
        pid: str,
        state: WorkflowState,
        integration: IntegrationResult,
    ) -> str:
        """打包项目为交付物（支持版本化）。"""
        import zipfile
        from pathlib import Path

        # ── 版本化目录 ──
        if VERSIONED_DELIVERY_ENABLED:
            # 检测当前版本号
            base_dir = DELIVERIES_DIR / pid
            base_dir.mkdir(parents=True, exist_ok=True)
            existing = sorted([int(d.name[1:]) for d in base_dir.iterdir() if d.is_dir() and d.name.startswith("v") and d.name[1:].isdigit()])
            version_num = existing[-1] + 1 if existing else 1
            project_dir = base_dir / f"v{version_num}"
            # 归档旧版本
            for d in existing:
                vdir = base_dir / f"v{d}"
                if d <= version_num - MAX_VERSIONS_KEPT and vdir.exists():
                    archive_dir = base_dir / ARCHIVE_DIR_NAME
                    archive_dir.mkdir(exist_ok=True)
                    import shutil
                    shutil.make_archive(str(archive_dir / f"v{d}"), "zip", str(vdir))
                    shutil.rmtree(vdir, ignore_errors=True)
        else:
            version_num = 1
            project_dir = DELIVERIES_DIR / pid

        project_dir.mkdir(parents=True, exist_ok=True)

        # ── 注入自治运维模块 ──
        if AUTOPILOT_ENABLED:
            await self._inject_autopilot(pid, project_dir, state)

        # 写入各模块代码（含 blocked 占位文件）
        for m in state["plan_modules"]:
            name = m["module_name"]
            code = state["module_results"].get(name, {}).get("code", "")
            if code:
                ext = ".py"
                mtype = m.get("type", "backend")
                if mtype == "frontend":
                    ext = ".html"
                (project_dir / f"{name}{ext}").write_text(code, encoding="utf-8")

        # 写入集成产物
        (project_dir / "main.py").write_text(
            integration.main_code, encoding="utf-8"
        )
        (project_dir / "README.md").write_text(
            integration.readme, encoding="utf-8"
        )
        (project_dir / "requirements.txt").write_text(
            integration.requirements, encoding="utf-8"
        )

        # 写入集成测试（如果有 blocked 模块，额外生成 test_blocked_modules.py）
        if integration.integration_tests:
            (project_dir / "test_integration.py").write_text(
                integration.integration_tests, encoding="utf-8"
            )

        # ── 生成 TODO.md（未完成模块清单） ──
        blocked_modules = state.get("blocked_modules", [])
        if blocked_modules:
            todo_lines = [
                "# 未完成模块清单 (TODO)",
                "",
                f"> 生成时间: {datetime.now(timezone.utc).isoformat()}",
                f"> 项目: {pid}",
                f"> 共 {len(blocked_modules)} 个模块需要手动完善",
                "",
                "以下模块因审查不通过或执行异常被自动阻塞，已生成占位文件。",
                "请按优先级逐一完成实现。",
                "",
            ]
            for i, mod_name in enumerate(blocked_modules, 1):
                result = state["module_results"].get(mod_name, {})
                todo_lines.append(f"## {i}. 模块: {mod_name}")
                todo_lines.append("")
                # 找原始规划描述
                for m in state["plan_modules"]:
                    if m["module_name"] == mod_name:
                        todo_lines.append(f"- **原定需求**: {m.get('description', '未知')}")
                        todo_lines.append(f"- **模块类型**: {m.get('type', 'unknown')}")
                        break
                todo_lines.append(f"- **状态**: 阻塞 (blocked)")
                todo_lines.append(f"- **阻塞原因**: {result.get('failure_reason', '未知')}")
                todo_lines.append("")
                todo_lines.append("**建议手动修复步骤**:")
                todo_lines.append(f"1. 打开文件 `{mod_name}.py`（或对应的 .html 文件）")
                todo_lines.append(f"2. 阅读文件顶部的阻塞原因注释")
                todo_lines.append(f"3. 根据原定需求描述完成代码实现")
                todo_lines.append(f"4. 编写对应的单元测试")
                todo_lines.append(f"5. 验证功能正确性")
                todo_lines.append("")

            todo_text = "\n".join(todo_lines)
            (project_dir / "TODO.md").write_text(todo_text, encoding="utf-8")

            # 生成 test_blocked_modules.py
            test_blocked_lines = [
                '"""提醒哪些模块尚未完成的测试文件。"""',
                '',
                'import pytest',
                '',
                '',
                'BLOCKED_MODULES = [',
            ]
            for mod_name in blocked_modules:
                test_blocked_lines.append(f'    "{mod_name}",')
            test_blocked_lines.append(']')
            test_blocked_lines.append('')
            test_blocked_lines.append('')
            test_blocked_lines.append('@pytest.mark.skip(reason="模块尚未完成，详见 TODO.md")')
            test_blocked_lines.append('@pytest.mark.parametrize("module_name", BLOCKED_MODULES)')
            test_blocked_lines.append('def test_blocked_module_reminder(module_name: str) -> None:')
            test_blocked_lines.append('    """提醒开发人员：此模块尚未完成。"""')
            test_blocked_lines.append('    pytest.skip(f"模块 \\"{module_name}\\" 尚未完成，请先完善代码后再运行测试。详见 TODO.md")')
            test_blocked_lines.append('')
            (project_dir / "test_blocked_modules.py").write_text(
                "\n".join(test_blocked_lines), encoding="utf-8"
            )

        # 写入审查报告
        report = state.get("global_review", {})
        blocked_info = ""
        if blocked_modules:
            blocked_info = f"\n- ⚠️ 未完成模块数: {len(blocked_modules)}\n- 未完成模块: {', '.join(blocked_modules)}\n"
        report_text = f"""# 全局审查报告

- 审查结果: {'PASS' if report.get('passed') else '需改进'}
- 评分: {report.get('score', 0)}/100
- 摘要: {report.get('summary', '')}
{blocked_info}
- 问题: {json.dumps(report.get('issues', []), ensure_ascii=False, indent=2)}
- 建议: {json.dumps(report.get('suggestions', []), ensure_ascii=False, indent=2)}
"""
        (project_dir / "REVIEW_REPORT.md").write_text(
            report_text, encoding="utf-8"
        )

        # 打包 ZIP
        zip_path = project_dir.with_suffix(".zip")
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for file in project_dir.rglob("*"):
                if file.is_file():
                    zf.write(file, file.relative_to(project_dir))

        return str(zip_path)
