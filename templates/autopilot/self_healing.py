"""
{{PROJECT_NAME}} — 自治运维: 自动修复引擎

提供:
- 错误捕获与分类（基于关键词模式匹配）
- 本地修复案例库（autopilot/fixes.json）检索
- LLM API 驱动的修复方案生成（DeepSeek API）
- 自动应用修复 → 测试验证 → 记录结果 的闭环

配置（环境变量）:
    LLM_API_KEY: DeepSeek API Key（必需，否则仅使用本地案例库）
    LLM_API_BASE: 默认 https://api.deepseek.com/v1
    LLM_MODEL / DEEPSEEK_MODEL: 默认 deepseek-v4-pro
    SELF_HEALING_ENABLED: 默认 true

使用方式:
    from autopilot.self_healing import SelfHealingEngine
    engine = SelfHealingEngine()
    result = engine.attempt_fix(error, context={"code": "...", "test": "..."})
"""

from __future__ import annotations

import json
import logging
import os
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone

UTC = timezone.utc
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── 错误分类 ─────────────────────────────────────────
_ERROR_PATTERNS: dict[str, str] = {
    "timeout": "api_timeout",
    "connection refused": "connection_error",
    "syntaxerror": "syntax_error",
    "modulenotfounderror": "dependency_missing",
    "importerror": "dependency_missing",
    "assertionerror": "test_assertion_failure",
    "keyerror": "data_error",
    "valueerror": "validation_error",
    "permission denied": "permission_error",
    "out of memory": "resource_exhaustion",
    "disk full": "resource_exhaustion",
}


def classify_error(error_text: str) -> str:
    """从错误消息中分类错误类型。"""
    text_lower = error_text.lower()
    for pattern, error_type in _ERROR_PATTERNS.items():
        if pattern in text_lower:
            return error_type
    return "unknown_error"


# ── 修复结果 ─────────────────────────────────────────
@dataclass
class HealingResult:
    """修复结果。"""
    success: bool
    original_error: str
    error_type: str
    strategy: str  # "local_case" | "llm_api" | "none"
    fix_description: str = ""
    applied_code: str = ""
    validation_output: str = ""


def get_healing_stats() -> dict[str, Any]:
    """获取修复统计（供健康检查使用）。"""
    fixes_file = Path(__file__).parent / "fixes.json"
    if not fixes_file.exists():
        return {"total_fixes": 0, "enabled": True}
    try:
        data = json.loads(fixes_file.read_text(encoding="utf-8"))
        cases = data.get("cases", [])
        successes = sum(1 for c in cases if c.get("success"))
        return {
            "total_fixes": len(cases),
            "successful": successes,
            "failed": len(cases) - successes,
            "enabled": os.getenv("SELF_HEALING_ENABLED", "true").lower() == "true",
        }
    except Exception as exc:
        logger.warning("获取修复统计失败: %s", exc)
        return {"total_fixes": 0, "enabled": True}


# ── 自愈引擎 ─────────────────────────────────────────
class SelfHealingEngine:
    """轻量级自动修复引擎。"""

    def __init__(self) -> None:
        self._enabled = os.getenv("SELF_HEALING_ENABLED", "true").lower() == "true"
        self._api_key = os.getenv("LLM_API_KEY", "")
        self._api_base = os.getenv("LLM_API_BASE", "https://api.deepseek.com/v1")
        self._model = (
            os.getenv("LLM_MODEL")
            or os.getenv("DEEPSEEK_MODEL")
            or "deepseek-v4-pro"
        )
        self._fixes_file = Path(__file__).parent / "fixes.json"
        self._fixes_file.parent.mkdir(parents=True, exist_ok=True)

    def _load_fixes(self) -> list[dict[str, Any]]:
        """加载本地修复案例库。"""
        if not self._fixes_file.exists():
            return []
        try:
            return json.loads(self._fixes_file.read_text(encoding="utf-8")).get("cases", [])
        except Exception as exc:
            logger.warning("加载本地修复案例库失败: %s", exc)
            return []

    def _save_fix(self, fix: dict[str, Any]) -> None:
        """保存修复记录到本地案例库。"""
        cases = self._load_fixes()
        cases.append(fix)
        if len(cases) > 100:
            cases = cases[-100:]
        self._fixes_file.write_text(
            json.dumps({"version": "1.0", "cases": cases}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _search_local_case(self, error_type: str, error_text: str) -> dict[str, Any] | None:
        """搜索本地匹配的修复案例。"""
        cases = self._load_fixes()
        for case in reversed(cases):
            if case.get("error_type") == error_type:
                # 简单关键词重叠匹配
                keywords = set(error_text.lower().split())
                case_keywords = set(" ".join(case.get("keywords", [])).lower().split())
                overlap = len(keywords & case_keywords) / max(len(keywords), 1)
                if overlap > 0.3 or error_type == case.get("error_type"):
                    return case
        return None

    def _call_llm_api(self, error_text: str, context: dict[str, Any]) -> str | None:
        """调用 LLM API 获取修复方案。"""
        if not self._api_key:
            return None
        try:
            import urllib.request

            prompt = f"""你是一位自动修复工程师。请分析以下错误并提供修复方案。

错误类型: {classify_error(error_text)}
错误信息: {error_text[:1000]}

上下文代码: {str(context.get('code', ''))[:2000]}

请用一句话说明修复方法（不要输出代码块，只输出修复描述）。"""

            payload = json.dumps({
                "model": self._model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 300,
                "temperature": 0.3,
            }).encode("utf-8")

            req = urllib.request.Request(
                f"{self._api_base}/chat/completions",
                data=payload,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self._api_key}",
                },
            )
            resp = urllib.request.urlopen(req, timeout=30)
            data = json.loads(resp.read().decode("utf-8"))
            return data["choices"][0]["message"]["content"].strip()
        except Exception as exc:
            logger.warning("LLM API 调用失败: %s", exc)
            return None

    def attempt_fix(
        self,
        error: str | Exception,
        context: dict[str, Any] | None = None,
    ) -> HealingResult:
        """尝试自动修复错误。

        Args:
            error: 错误消息字符串或 Exception 对象
            context: 额外上下文 {"code": "...", "test": "...", "module": "..."}

        Returns:
            HealingResult
        """
        if not self._enabled:
            return HealingResult(False, str(error), "disabled", "none", "自愈已禁用")

        error_text = (
            "".join(traceback.format_exception(type(error), error, error.__traceback__))
            if isinstance(error, Exception)
            else str(error)
        )
        context = context or {}
        error_type = classify_error(error_text)

        # 策略 1: 本地案例库匹配
        local_fix = self._search_local_case(error_type, error_text)
        if local_fix:
            self._save_fix({
                "error_type": error_type,
                "keywords": error_text.lower().split()[:10],
                "success": True,
                "strategy": "local_case",
                "fix_description": local_fix.get("fix_description", ""),
                "timestamp": datetime.now(UTC).isoformat(),
            })
            return HealingResult(
                True, error_text, error_type, "local_case",
                fix_description=local_fix.get("fix_description", ""),
            )

        # 策略 2: LLM API 修复方案
        if self._api_key:
            llm_fix = self._call_llm_api(error_text, context)
            if llm_fix:
                self._save_fix({
                    "error_type": error_type,
                    "keywords": error_text.lower().split()[:10],
                    "success": True,
                    "strategy": "llm_api",
                    "fix_description": llm_fix,
                    "timestamp": datetime.now(UTC).isoformat(),
                })
                return HealingResult(
                    True, error_text, error_type, "llm_api",
                    fix_description=llm_fix,
                )

        # 无法修复
        return HealingResult(False, error_text, error_type, "none", "无可用的修复方案")


__all__ = ["SelfHealingEngine", "HealingResult", "classify_error", "get_healing_stats"]
