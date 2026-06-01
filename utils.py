"""共享工具函数 — JSON 提取、LLM 客户端工厂等。

提供项目范围内可复用的基础工具，避免跨模块重复代码。
"""

from __future__ import annotations

import json
import re
from typing import Any

from langchain_openai import ChatOpenAI

from config import (
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    DEEPSEEK_MODEL,
    LLM_MAX_TOKENS,
    LLM_TEMPERATURE,
    LLM_TIMEOUT_SECONDS,
    LLM_MAX_RETRIES,
)


def extract_json(text: str) -> dict[str, Any]:
    """从 LLM 回复中提取 JSON 对象。

    会尝试多种策略：直接解析、提取 ```json 代码块、用正则找花括号区域。

    Args:
        text: LLM 返回的原始文本

    Returns:
        解析后的 JSON 字典

    Raises:
        ValueError: 所有策略均无法提取有效 JSON 时抛出
    """
    text = text.strip()

    # 策略 1：直接解析
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 策略 2：提取 ```json ... ``` 代码块
    match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    if match:
        try:
            return json.loads(match.group(1).strip())
        except json.JSONDecodeError:
            pass

    # 策略 3：找到最外层 { }
    brace_start = text.find("{")
    if brace_start != -1:
        depth = 0
        for i in range(brace_start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[brace_start : i + 1])
                    except json.JSONDecodeError:
                        break

    raise ValueError(f"无法从回复中提取有效 JSON。前 500 字符：{text[:500]}")


def create_llm(
    model: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    timeout: int | None = None,
    max_retries: int | None = None,
) -> ChatOpenAI:
    """创建配置好的 ChatOpenAI 实例（共享工厂）。

    所有参数可选，未提供时使用 config 中的默认值。

    Args:
        model: 模型名（默认 DEEPSEEK_MODEL）
        temperature: 模型温度（默认 LLM_TEMPERATURE）
        max_tokens: 最大 token 数（默认 LLM_MAX_TOKENS）
        timeout: HTTP 超时秒数（默认 LLM_TIMEOUT_SECONDS）
        max_retries: 失败重试次数（默认 LLM_MAX_RETRIES）

    Returns:
        配置完成的 ChatOpenAI 客户端实例
    """
    return ChatOpenAI(
        model=model if model is not None else DEEPSEEK_MODEL,
        api_key=DEEPSEEK_API_KEY,
        base_url=DEEPSEEK_BASE_URL,
        temperature=temperature if temperature is not None else LLM_TEMPERATURE,
        max_tokens=max_tokens if max_tokens is not None else LLM_MAX_TOKENS,
        timeout=timeout if timeout is not None else LLM_TIMEOUT_SECONDS,
        max_retries=max_retries if max_retries is not None else LLM_MAX_RETRIES,
    )
