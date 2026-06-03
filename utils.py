"""共享工具函数 — JSON 提取、LLM 客户端工厂等。

提供项目范围内可复用的基础工具，避免跨模块重复代码。
"""

from __future__ import annotations

import json
import re
from typing import Any, Literal

from langchain_openai import ChatOpenAI

from config import (
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    DEEPSEEK_MODEL,
    DEEPSEEK_REASONING_EFFORT,
    LLM_MAX_RETRIES,
    LLM_MAX_TOKENS,
    LLM_TEMPERATURE,
    LLM_TIMEOUT_SECONDS,
)

ThinkingMode = Literal["enabled", "disabled"]


def _strip_json_fence(text: str) -> str:
    """去掉 markdown 代码块围栏。"""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _try_parse_json_object(fragment: str) -> dict[str, Any] | None:
    """尝试解析 JSON 对象，失败时尝试补全截断的括号/引号。"""
    fragment = _strip_json_fence(fragment)
    if not fragment.startswith("{"):
        return None

    candidates = [fragment]
    # 截断响应：补闭合引号与花括号
    for suffix in ('"', '"}', '"}', '"}]}', '"}]}', '"}]}'):
        candidates.append(fragment + suffix)
    open_braces = fragment.count("{") - fragment.count("}")
    if open_braces > 0:
        candidates.append(fragment + ('"' if fragment.count('"') % 2 else "") + "}" * open_braces)

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            continue
    return None


def extract_json(text: str) -> dict[str, Any]:
    """从 LLM 回复中提取 JSON 对象。

    会尝试多种策略：直接解析、提取 ```json 代码块、用正则找花括号区域、
    补全截断 JSON。

    Args:
        text: LLM 返回的原始文本

    Returns:
        解析后的 JSON 字典

    Raises:
        ValueError: 所有策略均无法提取有效 JSON 时抛出
    """
    import logging

    _logger = logging.getLogger(__name__)
    text = text.strip()

    # 策略 1：直接解析
    parsed = _try_parse_json_object(text)
    if parsed is not None:
        return parsed

    # 策略 2：非贪婪 ```json ... ``` 代码块
    match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    if match:
        parsed = _try_parse_json_object(match.group(1))
        if parsed is not None:
            _logger.debug("extract_json: 通过 ```json 代码块提取成功")
            return parsed

    # 策略 2b：贪婪代码块（截断时可能没有闭合 ```）
    greedy = re.search(r"```(?:json)?\s*([\s\S]*)", text)
    if greedy:
        parsed = _try_parse_json_object(greedy.group(1).rstrip("`"))
        if parsed is not None:
            _logger.warning(
                "extract_json: LLM 输出可能被截断，通过补全策略提取 JSON。"
                "原始内容前 300 字符：%s",
                text[:300],
            )
            return parsed

    # 策略 3：找到最外层 { }
    brace_start = text.find("{")
    if brace_start != -1:
        depth = 0
        in_string = False
        escape = False
        for i in range(brace_start, len(text)):
            ch = text[i]
            if in_string:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    parsed = _try_parse_json_object(text[brace_start : i + 1])
                    if parsed is not None:
                        return parsed
                    break

        parsed = _try_parse_json_object(text[brace_start:])
        if parsed is not None:
            _logger.warning(
                "extract_json: LLM 输出花括号不匹配，通过截断补全策略提取。"
                "原始内容前 300 字符：%s",
                text[:300],
            )
            return parsed

    _logger.error(
        "extract_json: 所有策略均失败。原始内容前 500 字符：%s",
        text[:500],
    )
    raise ValueError(f"无法从回复中提取有效 JSON。前 500 字符：{text[:500]}")


def create_llm(
    model: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    timeout: int | None = None,
    max_retries: int | None = None,
    *,
    thinking: ThinkingMode | None = None,
    reasoning_effort: str | None = None,
    json_output: bool = False,
) -> ChatOpenAI:
    """创建配置好的 ChatOpenAI 实例（DeepSeek V4 兼容）。

    Args:
        model: 模型名（默认 DEEPSEEK_MODEL）
        temperature: 温度（thinking=disabled 时生效）
        max_tokens: 最大 token 数
        timeout: HTTP 超时秒数
        max_retries: 失败重试次数
        thinking: V4 thinking 开关（enabled / disabled）
        reasoning_effort: thinking=enabled 时的推理力度（high / max）
        json_output: 是否启用官方 JSON Output（response_format=json_object）

    Returns:
        配置完成的 ChatOpenAI 客户端实例
    """
    _mt = max_tokens if max_tokens is not None else LLM_MAX_TOKENS
    kwargs: dict[str, Any] = {
        "model": model if model is not None else DEEPSEEK_MODEL,
        "api_key": DEEPSEEK_API_KEY,
        "base_url": DEEPSEEK_BASE_URL,
        "timeout": timeout if timeout is not None else LLM_TIMEOUT_SECONDS,
        "max_retries": max_retries if max_retries is not None else LLM_MAX_RETRIES,
    }
    if _mt is not None:
        kwargs["max_tokens"] = _mt

    model_kwargs: dict[str, Any] = {}
    extra_body: dict[str, Any] = {}

    if json_output:
        model_kwargs["response_format"] = {"type": "json_object"}

    if thinking is not None:
        extra_body["thinking"] = {"type": thinking}
        if thinking == "enabled":
            extra_body["reasoning_effort"] = reasoning_effort or DEEPSEEK_REASONING_EFFORT
        else:
            kwargs["temperature"] = (
                temperature if temperature is not None else LLM_TEMPERATURE
            )
    else:
        kwargs["temperature"] = (
            temperature if temperature is not None else LLM_TEMPERATURE
        )

    if extra_body:
        kwargs["extra_body"] = extra_body
    if model_kwargs:
        kwargs["model_kwargs"] = model_kwargs

    return ChatOpenAI(**kwargs)


def create_llm_reasoning(
    *,
    model: str | None = None,
    max_tokens: int | None = None,
    timeout: int | None = None,
    max_retries: int | None = None,
    reasoning_effort: str | None = None,
    json_output: bool = True,
) -> ChatOpenAI:
    """规划/对齐类 Agent：V4 thinking + 可选 JSON Output。"""
    return create_llm(
        model=model,
        max_tokens=max_tokens,
        timeout=timeout,
        max_retries=max_retries,
        thinking="enabled",
        reasoning_effort=reasoning_effort,
        json_output=json_output,
    )


def create_llm_json(
    *,
    model: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    timeout: int | None = None,
    max_retries: int | None = None,
) -> ChatOpenAI:
    """结构化 JSON 输出 Agent：thinking 关闭 + json_object。"""
    return create_llm(
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
        max_retries=max_retries,
        thinking="disabled",
        json_output=True,
    )


def create_llm_text(
    *,
    model: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    timeout: int | None = None,
    max_retries: int | None = None,
) -> ChatOpenAI:
    """自由文本 Agent（如 PASS/FAIL 审查）：thinking 关闭。"""
    return create_llm(
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
        max_retries=max_retries,
        thinking="disabled",
        json_output=False,
    )
