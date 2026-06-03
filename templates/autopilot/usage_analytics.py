"""
{{PROJECT_NAME}} — 自治运维: 匿名使用分析与洞察生成

提供:
- 匿名使用数据收集（需用户 opt-in，环境变量 ANALYTICS_ENABLED=true）
- 本地 JSON 存储，不上传外部服务
- 周期性分析（每天检查一次），生成优化洞察报告
- 可选：自动将洞察发送给 DevFlow CI 获取优化建议

配置（环境变量）:
    ANALYTICS_ENABLED: 默认 false（需用户主动开启）
    ANALYTICS_REPORT_INTERVAL_HOURS: 默认 24
    DEVFLOW_CI_ENDPOINT: DevFlow CI 助手地址（用于自动获取优化建议）

使用方式:
    from autopilot.usage_analytics import track, generate_report
    track("api_call", endpoint="/users", status=200, duration_ms=45)
    report = generate_report()
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone

UTC = timezone.utc
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_ANALYTICS_FILE = Path(__file__).parent / ".analytics.json"
_ANALYTICS_ENABLED = os.getenv("ANALYTICS_ENABLED", "false").lower() == "true"
_REPORT_INTERVAL = int(os.getenv("ANALYTICS_REPORT_INTERVAL_HOURS", "24"))


def track(
    event_type: str,
    **kwargs: Any,
) -> None:
    """记录一条匿名使用事件。

    Args:
        event_type: 事件类型（如 api_call, error_handled, feature_used）
        **kwargs: 附加数据（如 endpoint, status, duration_ms）
    """
    if not _ANALYTICS_ENABLED:
        return

    event: dict[str, Any] = {
        "type": event_type,
        "timestamp": datetime.now(UTC).isoformat(),
    }
    # 仅保留安全的元数据字段
    safe_keys = {"endpoint", "status", "duration_ms", "feature_name", "error_type", "module"}
    for k, v in kwargs.items():
        if k in safe_keys and isinstance(v, str | int | float | bool):
            event[k] = v

    try:
        _ANALYTICS_FILE.parent.mkdir(parents=True, exist_ok=True)
        data: list[dict[str, Any]] = []
        if _ANALYTICS_FILE.exists():
            data = json.loads(_ANALYTICS_FILE.read_text(encoding="utf-8")).get("events", [])
        data.append(event)
        # 保留最近 5000 条
        if len(data) > 5000:
            data = data[-5000:]
        _ANALYTICS_FILE.write_text(
            json.dumps({"events": data, "last_updated": datetime.now(UTC).isoformat()}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:
        logger.warning("使用分析事件记录失败: %s", exc)
        pass  # 分析失败不应影响主流程


def generate_report() -> dict[str, Any]:
    """生成优化洞察报告。

    Returns:
        {
            "generated_at": "...",
            "total_events": 1234,
            "top_endpoints": [...],
            "error_rate": 0.03,
            "insights": ["建议添加缓存...", "建议优化慢端点..."],
            "suggestions_from_devflow": "..."
        }
    """
    if not _ANALYTICS_FILE.exists():
        return {"total_events": 0, "insights": [], "summary": "暂无分析数据"}

    data = json.loads(_ANALYTICS_FILE.read_text(encoding="utf-8"))
    events = data.get("events", [])

    if not events:
        return {"total_events": 0, "insights": [], "summary": "暂无事件"}

    # 统计
    types: dict[str, int] = {}
    endpoints: dict[str, int] = {}
    errors = 0
    total_duration = 0.0

    for e in events:
        types[e.get("type", "unknown")] = types.get(e.get("type", "unknown"), 0) + 1
        if e.get("endpoint"):
            endpoints[e["endpoint"]] = endpoints.get(e["endpoint"], 0) + 1
        if e.get("status") and isinstance(e["status"], int) and e["status"] >= 400:
            errors += 1
        total_duration += float(e.get("duration_ms", 0))

    # 生成洞察
    insights: list[str] = []
    if errors > 0:
        error_rate = errors / max(len(events), 1)
        if error_rate > 0.1:
            insights.append(f"⚠️ 错误率较高 ({error_rate:.1%})，建议检查日志定位根因并考虑启用自我修复")

    sorted_endpoints = sorted(endpoints.items(), key=lambda x: x[1], reverse=True)
    if sorted_endpoints:
        insights.append(f"最频繁调用的端点: {sorted_endpoints[0][0]} ({sorted_endpoints[0][1]} 次)，建议优先优化此路径")

    avg_duration = total_duration / max(len([e for e in events if e.get("duration_ms")]), 1)
    if avg_duration > 1000:
        insights.append(f"⚠️ 平均响应时间 {avg_duration:.0f}ms，建议排查性能瓶颈")

    # 联系 DevFlow CI 获取建议
    devflow_suggestion = ""
    devflow_url = os.getenv("DEVFLOW_CI_ENDPOINT", "")
    if devflow_url:
        try:
            import urllib.request
            payload = json.dumps({
                "event_summary": {
                    "total": len(events),
                    "by_type": types,
                    "error_count": errors,
                }
            }).encode("utf-8")
            req = urllib.request.Request(
                f"{devflow_url}/api/projects/{{PROJECT_ID}}/preferences",
                data=payload,
                headers={"Content-Type": "application/json"},
            )
            resp = urllib.request.urlopen(req, timeout=10)
            devflow_suggestion = resp.read().decode("utf-8")[:500]
        except Exception as exc:
            logger.warning("DevFlow CI 连接失败: %s", exc)
            devflow_suggestion = "无法连接到 DevFlow CI 助手"

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "total_events": len(events),
        "by_type": types,
        "top_endpoints": [
            {"endpoint": ep, "count": cnt} for ep, cnt in sorted_endpoints[:5]
        ],
        "error_count": errors,
        "error_rate": round(errors / max(len(events), 1), 3),
        "avg_duration_ms": round(avg_duration, 1),
        "insights": insights,
        "suggestions_from_devflow": devflow_suggestion or "未配置 DEVFLOW_CI_ENDPOINT",
        "summary": f"共 {len(events)} 个事件，错误率 {errors/max(len(events),1):.1%}",
    }


def should_generate_report() -> bool:
    """检查是否应该生成新报告。"""
    if not _ANALYTICS_FILE.exists():
        return False
    last = getattr(should_generate_report, "_last_check", 0)
    now = time.time()
    if now - last < _REPORT_INTERVAL * 3600:
        return False
    should_generate_report._last_check = now  # type: ignore[attr-defined]
    return True


__all__ = ["track", "generate_report", "should_generate_report"]
