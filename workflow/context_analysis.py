"""上下文分析 — 项目目录扫描、文档收集、LLM 摘要生成和数据库同步。

从 workflow.executor 中独立出来。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select as _sql_select

from database.db import async_session_factory
from database.models import ModuleStatus, ModuleTask, Project, ProjectLog

# ── 忽略目录列表 ──────────────────────────────────────────

# ── 忽略目录列表 ──────────────────────────────────────────

IGNORED_DIRS = {
    "node_modules", "__pycache__", ".git", ".svn", ".hg",
    ".venv", "venv", ".tox", ".eggs", "build", "dist",
    ".mypy_cache", ".pytest_cache", ".ruff_cache",
    ".next", ".nuxt", ".cache", "coverage",
    "egg-info", ".egg-info", "site-packages",
    "deliveries",
}

# 文档扫描：扩展提示词库 / 调研资料常见格式
DOC_EXTENSIONS = {".md", ".txt", ".rst", ".adoc", ".docx", ".json", ".yaml", ".yml", ".csv"}
DOC_PRIORITY_KEYWORDS = (
    "市场", "报告", "调研", "research", "market", "analysis", "商业", "business",
    "prompt", "提示词", "strategy", "战略", "竞品", "用户", "prd", "plan",
)
MAX_DOC_FILES = 30
MAX_DOC_CHARS_DEFAULT = 8000
MAX_DOC_CHARS_BUSINESS = 24000


def _doc_priority_score(rel_path: str) -> int:
    """文件名/路径优先级：市场报告、提示词库等靠前。"""
    lower = rel_path.lower().replace("\\", "/")
    score = 0
    for kw in DOC_PRIORITY_KEYWORDS:
        if kw in lower:
            score += 10
    name = lower.rsplit("/", 1)[-1]
    if name in ("readme.md", "readme.txt"):
        score += 3
    if "prompt" in lower or "提示" in lower:
        score += 5
    # 根目录文件优先于深层嵌套
    depth = lower.count("/")
    score -= depth
    return score


def _collect_doc_files(dir_path: Path) -> list[Path]:
    """递归收集文档文件并按业务优先级排序。"""
    doc_files: list[Path] = []
    for ext in DOC_EXTENSIONS:
        for fp in dir_path.glob(f"**/*{ext}"):
            parts = fp.relative_to(dir_path).parts
            if any(p in IGNORED_DIRS or p.startswith(".") for p in parts):
                continue
            doc_files.append(fp)
    doc_files = list({fp.resolve() for fp in doc_files})
    doc_files.sort(
        key=lambda p: (
            -_doc_priority_score(str(p.relative_to(dir_path))),
            str(p.relative_to(dir_path)).lower(),
        )
    )
    return doc_files[:MAX_DOC_FILES]


def _read_json_as_text(fp: Path) -> str:
    """将 JSON 提示词库转为可读文本供 LLM 摘要。"""
    try:
        raw = fp.read_text(encoding="utf-8", errors="ignore")
        data = json.loads(raw)
    except Exception:
        return fp.read_text(encoding="utf-8", errors="ignore")

    def _flatten(obj: Any, prefix: str = "") -> list[str]:
        lines: list[str] = []
        if isinstance(obj, dict):
            for k, v in obj.items():
                key = f"{prefix}.{k}" if prefix else str(k)
                if isinstance(v, (dict, list)):
                    lines.extend(_flatten(v, key))
                elif v is not None and str(v).strip():
                    lines.append(f"{key}: {str(v).strip()[:500]}")
        elif isinstance(obj, list):
            for i, item in enumerate(obj[:50]):
                lines.extend(_flatten(item, f"{prefix}[{i}]" if prefix else f"[{i}]"))
        else:
            if str(obj).strip():
                lines.append(f"{prefix}: {str(obj).strip()[:500]}" if prefix else str(obj)[:500])
        return lines

    flat = _flatten(data)
    header = f"# JSON 资料: {fp.name}\n"
    body = "\n".join(flat[:120])
    return header + body if body else raw[:4000]


def _read_doc_file_content(fp: Path) -> str:
    """读取单个文档文件内容。"""
    suffix = fp.suffix.lower()
    if suffix == ".docx":
        return _read_docx(fp)
    if suffix == ".json":
        return _read_json_as_text(fp)
    return fp.read_text(encoding="utf-8", errors="ignore")


def build_context_scan_meta(
    structured_context: dict[str, Any],
    *,
    directory: str = "",
) -> dict[str, Any]:
    """构建可持久化/展示的目录扫描元数据。"""
    analyzed = structured_context.get("analyzed_files", [])
    files_meta = [
        {
            "file": af.get("file", ""),
            "summary": (af.get("summary") or "")[:200],
        }
        for af in analyzed
    ]
    return {
        "directory": directory,
        "document_type": structured_context.get("document_type", "generic"),
        "file_count": len(analyzed),
        "files": files_meta,
        "total_chars_read": structured_context.get("total_chars_read", 0),
        "chars_limit": structured_context.get("chars_limit", MAX_DOC_CHARS_DEFAULT),
        "truncated": structured_context.get("truncated", False),
        "no_documentation_found": structured_context.get("no_documentation_found", False),
        "project_type": structured_context.get("project_type", ""),
        "source_file_count": len(structured_context.get("source_files", [])),
    }


async def persist_context_scan(project_id: str, scan: dict[str, Any]) -> None:
    """将目录扫描结果写入数据库并推送快照。"""
    try:
        from database.db import async_session_factory
        from database.models import Project
        from sqlalchemy import select as _sql_select

        async with async_session_factory() as db:
            result = await db.execute(
                _sql_select(Project).where(Project.project_id == project_id)
            )
            project = result.scalar_one_or_none()
            if project:
                project.context_scan_json = json.dumps(scan, ensure_ascii=False)
                await db.commit()
        await push_project_snapshot(project_id, force=True)
    except Exception:
        pass


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
            "finalized": ProjectStatus.FINALIZED,
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
                await push_project_snapshot(project_id, force=True)
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

    # ── 2. 收集文档文件（优先级排序） ──
    doc_files = _collect_doc_files(dir_path)

    # ── 3. 读取文档内容 ──
    file_contents: list[dict[str, Any]] = []
    total_chars = 0
    # 先粗判是否纯资料目录（无关键工程文件）→ 提高字符上限
    has_engineering_keys = any(
        k in key_files
        for k in (
            "requirements.txt", "pyproject.toml", "package.json",
            "app.py", "main.py", "Cargo.toml", "go.mod",
        )
    )
    max_doc_chars = MAX_DOC_CHARS_DEFAULT if has_engineering_keys else MAX_DOC_CHARS_BUSINESS
    truncated = False

    for fp in doc_files:
        try:
            content = _read_doc_file_content(fp)
            if len(content.strip()) < 30:
                continue
            if total_chars + len(content) > max_doc_chars:
                remaining = max_doc_chars - total_chars
                if remaining <= 200:
                    truncated = True
                    break
                content = content[:remaining] + "\n...(已截断)"
                truncated = True
            rel_path = str(fp.relative_to(dir_path))
            file_contents.append({"file": rel_path, "content": content})
            total_chars += len(content)
            if total_chars >= max_doc_chars:
                truncated = True
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
    summaries["total_chars_read"] = total_chars
    summaries["chars_limit"] = max_doc_chars
    summaries["truncated"] = truncated
    summaries["files_scanned"] = [fc["file"] for fc in file_contents]

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
    from utils import create_llm_json, extract_json

    llm = create_llm_json(temperature=0.1, max_tokens=2048, timeout=60, max_retries=1)

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
        result = extract_json(raw_text)
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


