"""模块级 Agent — 分析师、编码者、测试者。

单个模块的执行流程：分析 → 编码 → 测试
三个 Agent 通过结构化 Schema 传递数据。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from memory.case_store import CaseStore
from config import LLM_MAX_TOKENS
from utils import create_llm_json, create_llm_text, extract_json

MVP_SCOPE_NOTE = """
## MVP 范围（必须遵守）
- 单文件代码不超过 120 行，测试代码不超过 80 行
- 只实现一个核心函数或一个核心类，不要 SQLite/CLI/多子系统
- 规格若过大，只取第一项能力实现
"""


# ── 数据结构 ──────────────────────────────────────────────

@dataclass
class ModuleSpec:
    """模块分析规格。"""
    module_name: str
    summary: str                    # 功能概述
    api_endpoints: list[str] = field(default_factory=list)
    data_models: list[str] = field(default_factory=list)
    logic_flow: str = ""            # 核心逻辑流程
    error_handling: str = ""        # 异常场景


@dataclass
class ModuleCode:
    """编码产出。"""
    module_name: str
    code: str                       # 完整代码
    test_code: str                  # 测试代码
    language: str = "python"


@dataclass
class ModuleTestResult:
    """测试结果。"""
    module_name: str
    passed: bool
    details: str


# ── 系统提示词 ────────────────────────────────────────────

ANALYST_SYSTEM_PROMPT = """你是一位资深系统分析师。你的任务是为一个软件模块编写详细的功能规格。

## 输出格式（严格 JSON）

```json
{
  "module_name": "模块名",
  "summary": "一句话概述模块功能",
  "api_endpoints": ["GET /api/users - 获取用户列表", "POST /api/users - 创建用户"],
  "data_models": ["User: {id, username, email, password_hash, created_at}"],
  "logic_flow": "1. 接收请求 → 2. 校验输入 → 3. 业务处理 → 4. 返回响应",
  "error_handling": "用户名重复 → 409; 输入无效 → 422; 数据库异常 → 500"
}
```

## 规则
1. 输出必须是有效 JSON，不要添加额外说明。
2. api_endpoints 按 HTTP 方法 + 路径 + 简要描述列出。
3. data_models 描述每个模型的关键字段和类型。
4. 考虑鉴权、输入校验、错误处理。
"""

CODER_SYSTEM_PROMPT = """你是一位高级全栈开发工程师。你的任务是根据功能规格编写生产级代码。

## 输出格式（严格 JSON）

```json
{
  "module_name": "auth",
  "language": "python",
  "file_name": "auth.py",
  "code": "完整的 Python 代码，包含类型注解、docstring、异常处理",
  "test_code": "完整的 pytest 测试代码，覆盖核心逻辑和异常路径"
}
```

## 代码要求
1. 代码必须包含完整的 import 语句。
2. 所有公共函数/类必须包含 type hints 和 docstring。
3. 异常处理必须覆盖外部调用。
4. 不得包含硬编码的密钥或密码。
5. 不要使用 print()，使用 logging 模块。
6. test_code 使用 pytest 风格，覆盖至少 2 个正常路径和 1 个异常路径。
7. 输出必须是有效 JSON。代码内部的双引号用反斜杠转义。
8. 若标注 MVP，遵守单文件行数上限，优先可运行的小实现。
"""

TESTER_SYSTEM_PROMPT = """你是一位质量保证工程师。你的任务是审查代码和测试，判断是否通过。

## 输出格式（严格 JSON）

```json
{
  "module_name": "auth",
  "passed": true,
  "issues": [],
  "summary": "代码质量良好，测试覆盖充分"
}
```

或

```json
{
  "module_name": "auth",
  "passed": false,
  "issues": [
    "缺少输入校验：用户名未检查是否为空",
    "异常处理不完整：数据库连接失败未处理"
  ],
  "summary": "发现 2 个问题需要修复"
}
```

## 规则
1. 只有当代码确实满足所有要求时，passed 才为 true。
2. issues 列表每项一句话，具体指出问题。
3. 审查重点：类型注解、异常处理、安全性、测试覆盖。
"""


# ── Agent 类 ──────────────────────────────────────────────

class ModuleAgents:
    """模块级 Agent 组合：分析师 → 编码者 → 测试者。"""

    def __init__(self, case_store: CaseStore | None = None) -> None:
        self._case_store = case_store
        self._llm = create_llm_json()
        self._coder_llm = create_llm_json(max_tokens=LLM_MAX_TOKENS * 2)

    async def analyze(
        self, module_name: str, description: str, project_context: str = "",
        mvp_mode: bool = False,
    ) -> ModuleSpec:
        """分析师：生成模块功能规格。

        Args:
            module_name: 模块名称
            description: PM 对该模块的描述
            project_context: 项目全局上下文（如全局需求、技术栈）

        Returns:
            ModuleSpec 结构化规格
        """
        few_shot = ""
        if self._case_store and self._case_store.count > 0:
            few_shot = self._case_store.format_few_shot(description, top_k=1)

        mvp_section = MVP_SCOPE_NOTE if mvp_mode else ""

        prompt = f"""{ANALYST_SYSTEM_PROMPT}
{mvp_section}

{few_shot}

项目上下文：
{project_context if project_context else "无"}

模块名称：{module_name}
模块描述：{description}

请输出上述 JSON 格式的功能规格。"""

        response = await self._llm.ainvoke(prompt)
        raw_text = response.content if hasattr(response, "content") else str(response)
        data = extract_json(raw_text)

        return ModuleSpec(
            module_name=data.get("module_name", module_name),
            summary=data.get("summary", ""),
            api_endpoints=data.get("api_endpoints", []),
            data_models=data.get("data_models", []),
            logic_flow=data.get("logic_flow", ""),
            error_handling=data.get("error_handling", ""),
        )

    async def code(
        self,
        module_name: str,
        spec: ModuleSpec,
        test_feedback: str = "",
        mvp_mode: bool = False,
    ) -> ModuleCode:
        """编码者：根据规格编写代码。

        Args:
            module_name: 模块名称
            spec: 分析师产出的规格
            test_feedback: 前置测试反馈（重试时提供）

        Returns:
            ModuleCode 含代码和测试
        """
        spec_text = f"""模块名称：{spec.module_name}
功能概述：{spec.summary}
API 端点：{', '.join(spec.api_endpoints)}
数据模型：{', '.join(spec.data_models)}
核心流程：{spec.logic_flow}
异常场景：{spec.error_handling}"""

        feedback_section = ""
        if test_feedback:
            feedback_section = f"\n前次测试反馈（请修复以下问题）：\n{test_feedback}"

        mvp_section = MVP_SCOPE_NOTE if mvp_mode else ""
        compact_hint = (
            "\n上次 JSON 输出过长或被截断，请输出更精简的实现（单文件≤120行）。"
            if "JSON" in test_feedback or "截断" in test_feedback
            else ""
        )

        prompt = f"""{CODER_SYSTEM_PROMPT}
{mvp_section}

{spec_text}
{feedback_section}{compact_hint}

请输出上述 JSON 格式（module_name="{module_name}", language="python"）。"""

        last_error: ValueError | None = None
        for attempt in range(3):
            response = await self._coder_llm.ainvoke(prompt)
            raw_text = response.content if hasattr(response, "content") else str(response)
            try:
                data = extract_json(raw_text)
                return ModuleCode(
                    module_name=data.get("module_name", module_name),
                    code=data.get("code", ""),
                    test_code=data.get("test_code", ""),
                    language=data.get("language", "python"),
                )
            except ValueError as exc:
                last_error = exc
                prompt = f"""{CODER_SYSTEM_PROMPT}
{mvp_section}

{spec_text}
{feedback_section}

⚠️ 第 {attempt + 1} 次 JSON 解析失败：{exc}
请重新输出完整、有效的 JSON；代码务必精简（单文件≤120行），确保 JSON 可闭合。"""

        raise last_error or ValueError("编码 JSON 解析失败")

    async def test(
        self, module_name: str, code: ModuleCode, spec: ModuleSpec
    ) -> ModuleTestResult:
        """测试者：审查代码和测试的充分性。

        Args:
            module_name: 模块名称
            code: 编码产出
            spec: 原始规格

        Returns:
            ModuleTestResult
        """
        prompt = f"""{TESTER_SYSTEM_PROMPT}

模块名称：{module_name}
功能规格：{spec.summary}
核心流程：{spec.logic_flow}
异常场景：{spec.error_handling}

代码：
```python
{code.code[:3000]}
```

测试代码：
```python
{code.test_code[:2000]}
```

请审查并输出 JSON 结果。"""

        response = await self._llm.ainvoke(prompt)
        raw_text = response.content if hasattr(response, "content") else str(response)
        data = extract_json(raw_text)

        return ModuleTestResult(
            module_name=data.get("module_name", module_name),
            passed=data.get("passed", False),
            details=data.get("summary", ""),
        )
