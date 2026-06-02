"""PM Agent — 将用户需求拆解为可执行的模块任务列表。

负责：
1. 接收自然语言需求，拆解为结构化 JSON 子任务
2. 检测模块间依赖关系
3. 从成功案例库中检索相似历史作为 few-shot 参考
4. 自动判断拆解粒度（简单需求 1 个模块，复杂需求多个模块）
5. 支持项目上下文（现有代码分析结果）和项目记忆（历史开发记录）注入
"""

from __future__ import annotations

import json
import logging
from typing import Any

from config import (
    DEPENDENCY_INFERENCE_ENABLED,
    FEEDBACK_LEARNING_ENABLED,
    HUMAN_FIXES_FILE,
)
from memory.case_store import CaseStore
from utils import create_llm_reasoning, extract_json

# ── 系统提示词 ────────────────────────────────────────────

PLANNER_SYSTEM_PROMPT = """你是一位资深软件架构师 / PM。你的任务是将用户需求拆解为可独立开发的模块任务列表。

## 输出格式
你 **必须** 返回严格符合以下格式的 JSON（不要包含其他文字）：

```json
{
  "modules": [
    {
      "module_name": "auth",
      "description": "用户注册与登录模块，支持 JWT 认证",
      "dependencies": [],
      "type": "backend"
    },
    {
      "module_name": "article_api",
      "description": "文章 CRUD API，包含列表、详情、创建、更新、删除",
      "dependencies": ["auth"],
      "type": "backend"
    }
  ],
  "global_requirements": ["使用 SQLite 数据库", "所有 API 返回 JSON"]
}
```

## 规则
1. **module_name**：简短的英文标识符，使用 snake_case。
2. **description**：用中文写清楚模块需要实现什么。
3. **dependencies**：列出该模块依赖的其他模块的 module_name（依赖它们先完成）。
4. **type**：backend（后端API）/ frontend（前端界面）/ database（数据库模型）/ integration（集成）/ testing（测试）。
5. **自动粒度判断**：根据需求复杂度自行决定拆解粒度。
   - 简单需求（如单个函数、小型工具）：1-2 个模块即可
   - 中等需求（如 CRUD API）：2-4 个模块
   - 复杂需求（如完整博客/电商系统）：4-10 个模块
6. 考虑模块的先后顺序：基础模块（数据库、认证）在前，业务模块在后。
7. 必须确保无循环依赖。
8. 如果在现有项目上增量开发，优先复用和修改现有模块，避免新建重复模块。
"""

ALTERNATIVE_PLAN_PROMPT = """你是一位资深软件架构师。除了主方案外，请为同一需求生成一个**架构层面的备选方案**。

备选方案应：
1. 采用不同于主方案的设计思路（如不同技术选型、不同模块划分方式、不同数据流方向）
2. 同样保证功能完整、无循环依赖
3. 在 `comparison` 字段中对比两个方案的优劣

## 输出格式（严格 JSON）

```json
{
  "plan_a": {
    "modules": [...],
    "global_requirements": [...],
    "label": "方案A：分层架构",
    "approach": "按层次划分：数据层→业务层→API层"
  },
  "plan_b": {
    "modules": [...],
    "global_requirements": [...],
    "label": "方案B：领域驱动",
    "approach": "按业务领域划分：用户域→内容域→通知域"
  },
  "comparison": {
    "plan_a_pros": ["耦合度低", "职责清晰", "易于测试"],
    "plan_a_cons": ["文件数量较多", "简单功能也需要跨层"],
    "plan_b_pros": ["业务内聚高", "新人容易理解", "修改成本低"],
    "plan_b_cons": ["领域间可能有重复", "扩展性稍弱"]
  }
}
```

请确保两个方案的 modules 列表都符合相同的字段规范。"""

DEPENDENCY_INFERENCE_PROMPT = """你是一位 DevOps 工程师。请分析项目需求和技术栈，推断可能需要的第三方依赖库。

## 输出格式（严格 JSON，代表 dependencies.yaml 的结构）

```json
{
  "dependencies": [
    {
      "name": "fastapi",
      "version": ">=0.115.0",
      "purpose": "Web 框架，提供 REST API",
      "required": true
    },
    {
      "name": "uvicorn",
      "version": ">=0.30.0",
      "purpose": "ASGI 服务器",
      "required": true
    },
    {
      "name": "pytest",
      "version": ">=8.0",
      "purpose": "测试框架",
      "required": false,
      "note": "仅开发/测试环境需要"
    }
  ],
  "system_dependencies": [
    {
      "name": "python",
      "version": ">=3.11",
      "purpose": "运行时"
    }
  ],
  "summary": "共 3 个 Python 依赖，0 个系统依赖"
}
```

## 规则
1. 只列出确实需要的依赖，不要过度推断。
2. version 字段给出合理的版本范围。
3. required: true 表示运行时必需的，false 表示开发/可选依赖。
4. 考虑项目类型（Web API / CLI / 数据处理 / 前端等）。"""

logger = logging.getLogger(__name__)

# ── PM Agent ──────────────────────────────────────────────

class PlannerAgent:
    """项目管理 Agent。

    将用户需求拆解为 LangGraph 可执行的模块 DAG。
    """

    def __init__(self, case_store: CaseStore | None = None) -> None:
        """初始化 PM Agent。

        Args:
            case_store: 成功案例存储（可选）。提供时启用 few-shot 检索。
        """
        self._case_store = case_store
        self._llm = create_llm_reasoning()

    def _build_prompt(
        self,
        requirement: str,
        project_context: str = "",
        project_memory_text: str = "",
    ) -> str:
        """构建发送给 LLM 的完整 Prompt。

        Args:
            requirement: 用户输入的原始需求
            project_context: 项目目录上下文分析结果（技术栈、现有模块等）
            project_memory_text: 项目级历史记忆文本
        """
        parts: list[str] = [PLANNER_SYSTEM_PROMPT]

        # 注入 few-shot 案例
        if self._case_store and self._case_store.count > 0:
            few_shot = self._case_store.format_few_shot(requirement)
            parts.append(few_shot)

        # 注入项目上下文（来自上下文分析节点）
        if project_context:
            parts.append("## 📂 项目目录上下文分析")
            parts.append(project_context)
            parts.append("请在上述已有代码基础上进行增量规划。优先修改现有模块，只新建必要的新模块。")

        # 注入项目级记忆
        if project_memory_text:
            parts.append(project_memory_text)

        # 用户需求
        parts.append(f"## 用户需求\n{requirement}")
        parts.append("\n请根据需求复杂度自动判断拆解粒度，输出上述 JSON 格式的任务拆解结果。")

        return "\n\n".join(parts)

    @staticmethod
    def validate_plan(
        plan: dict[str, Any],
    ) -> list[str]:
        """验证规划结果。

        Returns:
            错误信息列表。空列表表示通过。
        """
        errors: list[str] = []

        if "modules" not in plan:
            errors.append("缺少 'modules' 字段")
            return errors

        modules = plan["modules"]
        if not isinstance(modules, list):
            errors.append("'modules' 必须是数组")
            return errors
        if len(modules) == 0:
            errors.append("'modules' 不能为空")
            return errors

        # 收集模块名
        names: set[str] = set()
        for i, m in enumerate(modules):
            name = m.get("module_name", "")
            if not name:
                errors.append(f"模块 #{i}: 缺少 module_name")
                continue
            if name in names:
                errors.append(f"模块名称重复: {name}")
            names.add(name)

            # 必填字段检查
            for field in ["description", "type"]:
                if not m.get(field):
                    errors.append(f"模块 '{name}': 缺少 '{field}'")

            # 依赖有效性
            deps = m.get("dependencies", [])
            if not isinstance(deps, list):
                errors.append(f"模块 '{name}': dependencies 必须是数组")

        # 循环依赖检测
        if not errors:
            cycle = PlannerAgent._detect_cycle(modules)
            if cycle:
                errors.append(f"检测到循环依赖: {' -> '.join(cycle)}")

        # 依赖目标存在性
        for m in modules:
            name = m.get("module_name", "")
            for dep in m.get("dependencies", []):
                if dep not in names:
                    errors.append(
                        f"模块 '{name}' 依赖不存在: '{dep}'"
                    )

        return errors

    @staticmethod
    def _detect_cycle(modules: list[dict[str, Any]]) -> list[str] | None:
        """简单的 DFS 循环检测。

        Returns:
            循环路径列表，无循环返回 None
        """
        name_to_idx = {
            m.get("module_name", f"__anon_{i}"): i
            for i, m in enumerate(modules)
        }
        adj: list[list[int]] = [[] for _ in modules]
        for i, m in enumerate(modules):
            for dep in m.get("dependencies", []):
                if dep in name_to_idx:
                    adj[name_to_idx[dep]].append(i)

        WHITE, GRAY, BLACK = 0, 1, 2
        color = [WHITE] * len(modules)
        path: list[str] = []

        def dfs(u: int) -> bool:
            color[u] = GRAY
            module_name = modules[u].get("module_name", f"__anon_{u}")
            path.append(module_name)
            for v in adj[u]:
                if color[v] == GRAY:
                    path.append(modules[v].get("module_name", f"__anon_{v}"))
                    return True
                if color[v] == WHITE and dfs(v):
                    return True
            path.pop()
            color[u] = BLACK
            return False

        for i in range(len(modules)):
            if color[i] == WHITE and dfs(i):
                return path
        return None

    async def plan(
        self,
        requirement: str,
        project_context: str = "",
        project_memory_text: str = "",
    ) -> tuple[dict[str, Any], list[str]]:
        """执行规划（单方案，兼容旧接口）。"""
        prompt = self._build_prompt(requirement, project_context, project_memory_text)
        response = await self._llm.ainvoke(prompt)
        raw_text: str = response.content if hasattr(response, "content") else str(response)
        plan = extract_json(raw_text)
        errors = self.validate_plan(plan)
        return plan, errors

    async def plan_alternatives(
        self,
        requirement: str,
        project_context: str = "",
        project_memory_text: str = "",
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], list[str]]:
        """生成方案A/B对比。Returns: (plan_a, plan_b, comparison, errors)"""
        plan_a, errors = await self.plan(requirement, project_context, project_memory_text)
        if errors:
            return plan_a, {}, {}, errors
        alt_prompt = self._build_prompt(requirement, project_context, project_memory_text)
        alt_prompt += "\n\n" + ALTERNATIVE_PLAN_PROMPT
        alt_prompt += f"\n\n## 用户需求\n{requirement}"
        try:
            response = await self._llm.ainvoke(alt_prompt)
            raw_text: str = response.content if hasattr(response, "content") else str(response)
            alt_data = extract_json(raw_text)
            plan_a = alt_data.get("plan_a", plan_a)
            plan_b = alt_data.get("plan_b", {})
            comparison = alt_data.get("comparison", {})
            plan_a["comparison"] = comparison
            if plan_b:
                plan_b["comparison"] = comparison
                b_errors = self.validate_plan(plan_b)
                if b_errors:
                    return plan_a, {}, comparison, errors
            return plan_a, plan_b, comparison, []
        except Exception as exc:
            logger.warning("备选方案解析失败: %s", exc)
            return plan_a, {}, {}, errors

    async def infer_dependencies(self, requirement: str, project_context: str = "", project_type: str = "python") -> dict[str, Any]:
        """推断项目依赖。Returns dependencies.yaml 结构的 dict。"""
        if not DEPENDENCY_INFERENCE_ENABLED:
            return {"dependencies": [], "system_dependencies": [], "summary": "推断已禁用"}
        prompt = f"""{DEPENDENCY_INFERENCE_PROMPT}

项目类型: {project_type}
项目上下文: {project_context[:1000] if project_context else "无"}
用户需求: {requirement}

请输出 JSON 格式的依赖推断结果。"""
        try:
            response = await self._llm.ainvoke(prompt)
            raw_text: str = response.content if hasattr(response, "content") else str(response)
            return extract_json(raw_text)
        except Exception as exc:
            logger.warning("依赖推断失败: %s", exc)
            return {"dependencies": [], "system_dependencies": [], "summary": "推断失败"}

    @staticmethod
    def load_human_preferences(top_k: int = 3) -> str:
        """加载人工修改偏好，格式化为 prompt 注入文本。"""
        if not FEEDBACK_LEARNING_ENABLED:
            return ""
        try:
            data = json.loads(HUMAN_FIXES_FILE.read_text(encoding="utf-8"))
            fixes = data.get("fixes", [])
            if not fixes:
                return ""
            recent = fixes[-min(top_k, len(fixes)):]
            lines = ["\n## 👤 人类偏好参考（最近人工修改记录）\n"]
            for f in recent:
                lines.append(f"- **修改类型**: {f.get('fix_type', 'Unknown')} — {f.get('description', '')[:80]}")
            return "\n".join(lines)
        except Exception as exc:
            logger.warning("加载人类偏好失败: %s", exc)
            return ""
