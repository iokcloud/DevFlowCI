"""DevFlow CI 全局配置模块。

集中管理所有环境变量、LLM 参数、路径配置和系统常量。
"""

import enum
import os
from pathlib import Path
from dotenv import load_dotenv

# 加载 .env 文件（如果存在）
load_dotenv()

# ── 项目路径 ──────────────────────────────────────────────
PROJECT_ROOT: Path = Path(__file__).parent.resolve()
DELIVERIES_DIR: Path = PROJECT_ROOT / "deliveries"
DATABASE_DIR: Path = PROJECT_ROOT / "database"
MEMORY_DIR: Path = PROJECT_ROOT / "memory"
DOCS_DIR: Path = PROJECT_ROOT / "docs"
STATIC_DIR: Path = PROJECT_ROOT / "static"
SUCCESS_CASES_FILE: Path = DOCS_DIR / "success_cases.json"
AUTO_FIX_CASES_FILE: Path = MEMORY_DIR / "auto_fix_cases.json"
PROJECT_MEMORY_DIR: Path = MEMORY_DIR / "project_memory"  # 项目级记忆目录

# 确保关键目录存在
DELIVERIES_DIR.mkdir(parents=True, exist_ok=True)
DATABASE_DIR.mkdir(parents=True, exist_ok=True)
MEMORY_DIR.mkdir(parents=True, exist_ok=True)
PROJECT_MEMORY_DIR.mkdir(parents=True, exist_ok=True)

# ── 错误分类枚举 ──────────────────────────────────────────


class ErrorType(str, enum.Enum):
    """可自动修复的错误类型。"""
    API_TIMEOUT = "api_timeout"                   # API 调用超时
    API_RATE_LIMIT = "api_rate_limit"             # API 速率限制
    SYNTAX_ERROR = "syntax_error"                 # 语法错误
    TEST_FAILURE = "test_failure"                 # 测试失败（审查/断言层面）
    TEST_EXECUTION_FAILURE = "test_execution_failure"  # 测试执行失败（运行时崩溃）
    REVIEW_FAIL = "review_fail"                   # 审查不通过
    DEPENDENCY_MISSING = "dependency_missing"      # 依赖缺失
    UNKNOWN = "unknown"                           # 未分类错误

# ── DeepSeek / LLM 配置 ───────────────────────────────────
DEEPSEEK_API_KEY: str = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL: str = os.getenv(
    "DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"
)
DEEPSEEK_MODEL: str = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

# LLM 调用参数
LLM_TEMPERATURE: float = 0.3       # 主 Agent 用较低温度保证输出稳定
LLM_MAX_TOKENS: int = 4096
LLM_TIMEOUT_SECONDS: int = 120     # HTTP 超时
LLM_MAX_RETRIES: int = 3           # 失败重试次数

# ── 数据库配置 ────────────────────────────────────────────
DATABASE_URL: str = os.getenv(
    "DATABASE_URL",
    f"sqlite+aiosqlite:///{DATABASE_DIR / 'devflow.db'}",
)

# ── 工作流配置 ────────────────────────────────────────────
MAX_REVIEW_RETRIES: int = 5        # 单个模块审查最大重试次数（永不卡死策略：提高到5次）
MAX_CONCURRENT_MODULES: int = 4    # 并行模块数上限
TASK_TIMEOUT_SECONDS: int = 600    # 单个模块超时（10分钟）
MAX_NON_MODULE_RETRIES: int = 3    # 非模块阶段（规划/集成/全局审查）最大重试次数
CONTEXT_ANALYSIS_TOKEN_LIMIT: int = 3000  # 上下文分析节点读取文件的最大 token 数
CONTEXT_ANALYSIS_MAX_DEPTH: int = 3       # 上下文分析目录树深度限制

# ── 记忆系统配置 ──────────────────────────────────────────
MAX_SUCCESS_CASES: int = 100       # 成功案例库最大容量
SIMILARITY_TOP_K: int = 3          # 检索相似案例数量

# ── 自动化测试配置 ────────────────────────────────────────
TEST_EXECUTION_ENABLED: bool = True        # 是否启用实际测试执行
TEST_UNIT_TIMEOUT: int = 30                # 单元测试超时（秒）
TEST_INTEGRATION_TIMEOUT: int = 60         # 集成测试超时（秒）
TEST_FULL_SUITE_TIMEOUT: int = 120         # 全量测试超时（秒）
TEST_MAX_FAILURES_BEFORE_WARN: int = 2     # 交付前全量测试允许的最大失败数
TEST_USE_SANDBOX: bool = True              # 是否使用沙箱隔离目录
# ⚠️ 测试执行的依赖安装是破坏性操作，仅对 deliveries 目录生效

# ── 自动修复（异常自愈）配置 ──────────────────────────────
AUTO_FIX_ENABLED: bool = True              # 是否启用自动修复
AUTO_FIX_MAX_TOTAL_ROUNDS: int = 8         # 单模块自动修复总轮次上限（含原始重试）
AUTO_FIX_QUICK_REVIEW_MAX: int = 2         # 修复后快速审查最大次数
AUTO_FIX_CASE_TOP_K: int = 3               # 检索历史案例数
AUTO_FIX_RETRY_BASE_DELAY: float = 2.0     # 退避重试基础延迟（秒）
AUTO_FIX_RETRY_MAX_DELAY: float = 60.0     # 退避重试最大延迟（秒）
AUTO_FIX_RETRY_BACKOFF: float = 2.0        # 退避因子（指数退避基数）

# 修复策略优先级顺序
AUTO_FIX_STRATEGIES: list[str] = [
    "direct_retry",         # a) 直接重试（带指数退避）
    "modify_code",          # b) 修改代码/测试
    "repair_agent",         # c) RepairAgent 反思修复
    "history_case",         # d) 检索历史案例修复
    "block_with_stub",      # e) 生成占位文件并阻塞
]

# 错误类型 → 策略映射
ERROR_FIX_STRATEGY_MAP: dict[str, list[str]] = {
    "api_timeout":              ["direct_retry", "block_with_stub"],
    "api_rate_limit":           ["direct_retry", "block_with_stub"],
    "syntax_error":             ["modify_code", "repair_agent", "history_case", "block_with_stub"],
    "test_failure":             ["modify_code", "repair_agent", "history_case", "block_with_stub"],
    "test_execution_failure":   ["modify_code", "repair_agent", "history_case", "block_with_stub"],
    "review_fail":              ["modify_code", "repair_agent", "history_case", "block_with_stub"],
    "dependency_missing":       ["modify_code", "repair_agent", "block_with_stub"],
    "unknown":                  ["repair_agent", "history_case", "block_with_stub"],
}

# ── 智能规划与多方案配置 ──────────────────────────────────
PLAN_AUTO_CONFIRM_TIMEOUT: int = 120   # 用户方案选择超时（秒），超时自动选方案A
PLAN_MAX_ALTERNATIVES: int = 2         # 最多生成几个备选方案

# ── 需求对齐配置 ──────────────────────────────────────────
ALIGNMENT_TIMEOUT_SECONDS: int = 300   # 对齐阶段等待用户确认超时（秒），超时保持在 aligned 状态不自动执行
ALIGNMENT_ENABLE_MULTI_PLAN: bool = True  # 是否在需求对齐阶段生成多个方案供用户切换

# ── 商业 MVP 配置 ─────────────────────────────────────────
BUSINESS_MVP_MAX_MODULES: int = int(os.getenv("BUSINESS_MVP_MAX_MODULES", "3"))

# ── 依赖推断配置 ──────────────────────────────────────────
DEPENDENCY_INFERENCE_ENABLED: bool = True  # 是否启用依赖智能推断
DEPENDENCIES_FILE_NAME: str = "dependencies.yaml"  # 依赖文件名

# ── 版本化交付配置 ────────────────────────────────────────
VERSIONED_DELIVERY_ENABLED: bool = True  # 是否启用版本化交付
MAX_VERSIONS_KEPT: int = 5               # 保留最近 N 个版本，更旧归档
ARCHIVE_DIR_NAME: str = "archive"        # 归档目录名

# ── 反馈学习配置 ──────────────────────────────────────────
FEEDBACK_LEARNING_ENABLED: bool = True   # 是否启用人工反馈学习
HUMAN_FIXES_FILE: Path = MEMORY_DIR / "human_fixes.json"
MAX_HUMAN_FIXES: int = 200               # 最大人工修改记录数
PREFERENCE_REPORT_INTERVAL: int = 20     # 每积累 N 条生成偏好报告

# ── 自治项目孵化配置 ──────────────────────────────────────
AUTOPILOT_ENABLED: bool = True             # 是否在生成项目中注入自治模块
AUTOPILOT_TEMPLATES_DIR: Path = PROJECT_ROOT / "templates" / "autopilot"
TEMPLATES_DIR: Path = PROJECT_ROOT / "templates"

# ── 日志配置 ──────────────────────────────────────────────
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
