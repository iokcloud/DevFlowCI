"""
{{PROJECT_NAME}} — 自治运维模块 (Autopilot)

本模块由 DevFlow CI 自动生成，为项目提供开箱即用的自我运维能力：
- health_check: 健康检查端点与函数
- logger: 结构化 JSON 日志
- self_healing: 自动修复引擎
- feature_flag: 特性开关管理
- usage_analytics: 匿名使用分析与洞察

启用方式:
    所有模块通过环境变量控制开关，默认不收集任何数据。
    详见各模块文档注释。
"""

__version__ = "{{PROJECT_VERSION}}"
