"""自动化测试执行引擎。

提供：
1. run_module_tests(): 在沙箱中执行模块单元测试
2. run_integration_tests(): 执行集成测试
3. run_full_test_suite(): 执行交付前全量测试
4. TestResult: 标准化测试结果数据结构
5. 超时控制、降级语法检查、依赖自动安装
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── 数据结构 ──────────────────────────────────────────────

@dataclass
class TestCaseResult:
    """单个测试用例结果。"""
    name: str
    passed: bool
    error_message: str = ""
    duration_seconds: float = 0.0


@dataclass
class TestResult:
    """完整的测试运行结果。"""
    total: int = 0
    passed: int = 0
    failed: int = 0
    errors: int = 0
    cases: list[TestCaseResult] = field(default_factory=list)
    execution_output: str = ""          # subprocess 原始输出
    summary: str = ""                   # 人类可读摘要
    execution_mode: str = "none"        # "pytest" | "syntax_check" | "skipped"
    test_report_path: str = ""          # 生成的报告路径

    @property
    def all_passed(self) -> bool:
        return (
            self.total > 0
            and self.passed == self.total
            and self.failed == 0
            and self.errors == 0
        )

    @property
    def has_results(self) -> bool:
        return self.total > 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "errors": self.errors,
            "summary": self.summary,
            "execution_mode": self.execution_mode,
            "execution_output": self.execution_output[:2000],
            "cases": [
                {
                    "name": c.name,
                    "passed": c.passed,
                    "error_message": c.error_message[:200],
                    "duration_seconds": round(c.duration_seconds, 3),
                }
                for c in self.cases[:50]  # 最多保存 50 个用例
            ],
        }


# ── 辅助函数 ──────────────────────────────────────────────

def _is_pytest_available() -> bool:
    """检查当前环境是否可运行 pytest。"""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "--version"],
            capture_output=True, timeout=5,
        )
        return result.returncode == 0
    except Exception as exc:
        logger.warning("pytest 版本检查失败: %s", exc)
        return False


def _check_syntax(code: str, file_name: str = "test_module.py") -> TestResult:
    """降级方案：对代码做语法检查。

    Args:
        code: Python 源代码
        file_name: 文件名（仅用于标注）

    Returns:
        TestResult — passed 表示语法无误
    """
    result = TestResult(execution_mode="syntax_check")
    try:
        compile(code, file_name, "exec")
        result.total = 1
        result.passed = 1
        result.summary = "语法检查通过 (降级方案，未实际运行)"
        result.cases.append(TestCaseResult(name="syntax_check", passed=True))
    except SyntaxError as exc:
        result.total = 1
        result.failed = 1
        result.summary = f"语法错误: {exc.msg} at line {exc.lineno}"
        result.cases.append(TestCaseResult(
            name="syntax_check", passed=False,
            error_message=f"SyntaxError at line {exc.lineno}: {exc.msg}",
        ))
    return result


def _extract_test_case_names(code: str) -> list[str]:
    """从测试代码中提取测试函数名。"""
    pattern = re.compile(r"def\s+(test_\w+)\s*\(", re.MULTILINE)
    return [m.group(1) for m in pattern.finditer(code)]


def _extract_pytest_error_excerpt(output_text: str, limit: int = 800) -> str:
    """从 pytest 输出中提取最有用的错误片段。"""
    if not output_text:
        return ""
    markers = (
        "ModuleNotFoundError:",
        "ImportError:",
        "SyntaxError:",
        "ERROR collecting",
        "FAILED ",
        "AssertionError:",
        "E   ",
    )
    lines = output_text.splitlines()
    for i, line in enumerate(lines):
        if any(m in line for m in markers):
            return "\n".join(lines[i: i + 12]).strip()[:limit]
    return output_text.strip()[:limit]


def _build_failure_summary(output_text: str, returncode: int | None = None) -> str:
    excerpt = _extract_pytest_error_excerpt(output_text)
    if excerpt:
        rc = f"（exit={returncode}）" if returncode not in (None, 0) else ""
        return f"pytest 执行失败{rc}：\n{excerpt}"
    if output_text.strip():
        return f"pytest 执行失败：\n{output_text.strip()[:800]}"
    return (
        "pytest 无输出（可能测试文件无 def test_ 函数、依赖缺失或沙箱启动失败）"
    )


def format_test_failure_feedback(result: TestResult | dict[str, Any] | None) -> str:
    """将测试结果格式化为编码 Agent 可理解的修复反馈。"""
    if not result:
        return "单元测试未通过（无结果）"
    if isinstance(result, TestResult):
        data = result.to_dict()
        output = result.execution_output
    else:
        data = result
        output = data.get("execution_output") or ""

    parts: list[str] = []
    summary = (data.get("summary") or "").strip()
    if summary and not summary.startswith("✅"):
        parts.append(summary)

    for case in data.get("cases") or []:
        if case.get("passed"):
            continue
        name = case.get("name") or "unknown"
        err = (case.get("error_message") or "").strip()
        parts.append(f"失败用例 {name}: {err or '见 pytest 输出'}")

    if output.strip():
        excerpt = _extract_pytest_error_excerpt(output, limit=1200)
        if excerpt and excerpt not in "\n".join(parts):
            parts.append(excerpt)

    if not parts:
        return summary or "单元测试未通过（无详细输出，请检查 import 与 def test_ 命名）"
    return "\n".join(parts)


def _parse_pytest_output(output_text: str, known_tests: list[str]) -> TestResult:
    """解析 pytest 文本输出为 TestResult。

    Args:
        output_text: pytest 的 stdout+stderr
        known_tests: 已知的测试函数名列表

    Returns:
        TestResult
    """
    result = TestResult(execution_mode="pytest", execution_output=output_text[:3000])

    # 查找 passed/failed 统计行
    stat_match = re.search(
        r"(\d+)\s+passed.*?(\d+)\s+failed.*?(\d+)\s+errors?",
        output_text, re.IGNORECASE,
    )
    if stat_match:
        result.passed = int(stat_match.group(1))
        result.failed = int(stat_match.group(2))
        result.errors = int(stat_match.group(3))
        result.total = result.passed + result.failed + result.errors
    else:
        # Fallback: 尝试其他格式
        passed_m = re.search(r"(\d+)\s+passed", output_text, re.IGNORECASE)
        failed_m = re.search(r"(\d+)\s+failed", output_text, re.IGNORECASE)
        error_m = re.search(r"(\d+)\s+errors?\b", output_text, re.IGNORECASE)
        error_in_m = re.search(r"(\d+)\s+error(?:s)?\s+in\s+", output_text, re.IGNORECASE)
        coll_err_m = re.search(
            r"collected\s+\d+\s+items\s*/\s*(\d+)\s+error",
            output_text,
            re.IGNORECASE,
        )
        if passed_m or failed_m or error_m or error_in_m or coll_err_m:
            result.passed = int(passed_m.group(1)) if passed_m else 0
            result.failed = int(failed_m.group(1)) if failed_m else 0
            if error_m:
                result.errors = int(error_m.group(1))
            elif error_in_m:
                result.errors = int(error_in_m.group(1))
            elif coll_err_m:
                result.errors = int(coll_err_m.group(1))
            result.total = result.passed + result.failed + result.errors

    # 收集阶段失败但未解析到 errors
    if result.errors == 0 and (
        "ERROR collecting" in output_text
        or "Interrupted:" in output_text
        or "!!!" in output_text and "error" in output_text.lower()
    ):
        result.errors = max(result.errors, 1)
        if result.total == 0:
            result.total = result.errors

    # 提取失败用例
    if result.failed > 0 or result.errors > 0:
        for match in re.finditer(r"FAILED\s+(.+?)(?:\n|$)", output_text):
            case_name = match.group(1).strip()
            pos = match.end()
            next_lines = output_text[pos:pos + 300]
            error_line = next_lines.split("\n")[0].strip()
            result.cases.append(TestCaseResult(
                name=case_name, passed=False,
                error_message=error_line[:200],
            ))
        if not result.cases and result.errors > 0:
            err_m = re.search(
                r"ERROR collecting\s+(\S+)",
                output_text,
                re.IGNORECASE,
            )
            name = err_m.group(1) if err_m else "collection"
            result.cases.append(TestCaseResult(
                name=name,
                passed=False,
                error_message=_extract_pytest_error_excerpt(output_text, 200),
            ))

    # 提取通过用例
    for match in re.finditer(r"PASSED\s+(.+?)(?:\n|$)", output_text):
        result.cases.append(TestCaseResult(
            name=match.group(1).strip(), passed=True,
        ))

    # 如果 total 仍为 0，尝试统计（勿用 known_tests 伪造通过）
    if result.total == 0:
        result.total = result.passed + result.failed + result.errors
        if result.total == 0 and known_tests and "collected 0 items" not in output_text:
            result.total = len(known_tests)

    # 构建摘要
    if result.all_passed:
        result.summary = f"✅ 全部 {result.passed}/{result.total} 通过"
    elif result.total > 0:
        result.summary = (
            f"{result.passed} 通过, {result.failed} 失败, {result.errors} 错误 (共 {result.total})"
        )
        if result.passed == 0 and (result.failed or result.errors):
            excerpt = _extract_pytest_error_excerpt(output_text, 400)
            if excerpt:
                result.summary += f"\n{excerpt}"
    else:
        result.summary = _build_failure_summary(output_text)

    return result


async def _install_sandbox_deps(sandbox: Path, module_code: str, test_code: str) -> None:
    """按模块/测试 import 推断并安装缺失依赖到沙箱。"""
    from workflow.iteration_automation import infer_extra_requirements

    packages = infer_extra_requirements("", f"{module_code}\n{test_code}")
    if not packages:
        return
    req_file = sandbox / "requirements.txt"
    req_file.write_text("\n".join(packages) + "\n", encoding="utf-8")
    try:
        proc = await asyncio.wait_for(
            asyncio.create_subprocess_exec(
                sys.executable, "-m", "pip", "install",
                "-r", str(req_file),
                "--quiet", "--target", str(sandbox / ".deps"),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            ),
            timeout=60,
        )
        await proc.communicate()
    except Exception as exc:
        logger.warning("pip install 失败: %s", exc)
        pass

# ── 核心执行函数 ──────────────────────────────────────────

async def run_module_tests(
    module_name: str,
    test_code: str,
    project_id: str = "",
    timeout: int = 30,
    log_callback=None,
    module_code: str = "",
    module_filename: str = "",
) -> TestResult:
    """在沙箱中执行模块的单元测试。

    Args:
        module_name: 模块名称
        test_code: pytest 测试代码
        project_id: 项目 ID（用于沙箱目录）
        timeout: 超时秒数
        log_callback: async fn(level, message, module_name)
        module_code: 被测模块源码（写入沙箱供 import，避免 ImportError 误报）
        module_filename: 模块文件名，默认 ``{module_name}.py``

    Returns:
        TestResult
    """
    if not test_code or not test_code.strip():
        return TestResult(
            execution_mode="skipped",
            summary="无测试代码，跳过执行",
        )

    async def _log(level: str, msg: str) -> None:
        if log_callback:
            await log_callback(level, msg, module_name)

    # 检查 pytest 是否可用
    if not _is_pytest_available():
        await _log("WARN", "[测试执行] pytest 不可用，降级为语法检查")
        return _check_syntax(test_code, f"test_{module_name}.py")

    # 创建沙箱目录
    sandbox = Path(tempfile.gettempdir()) / "devflow_test_env" / project_id / module_name
    sandbox.mkdir(parents=True, exist_ok=True)

    if module_code and module_code.strip():
        src_name = module_filename or f"{module_name}.py"
        (sandbox / src_name).write_text(module_code, encoding="utf-8")

    test_file = sandbox / f"test_{module_name}.py"
    test_file.write_text(test_code, encoding="utf-8")

    # 创建 conftest.py（空文件，避免 pytest 警告）
    conftest = sandbox / "conftest.py"
    if not conftest.exists():
        conftest.touch()

    await _install_sandbox_deps(sandbox, module_code, test_code)

    deps_dir = sandbox / ".deps"
    pythonpath = str(sandbox)
    if deps_dir.is_dir():
        pythonpath = f"{deps_dir}{os.pathsep}{pythonpath}"

    await _log("INFO", f"[测试执行] 运行 pytest: {test_file.name}")

    try:
        proc = await asyncio.wait_for(
            asyncio.create_subprocess_exec(
                sys.executable, "-m", "pytest",
                str(test_file), "-v", "--tb=short",
                "--color=no",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=str(sandbox),
                env={
                    **os.environ,
                    "PYTHONPATH": pythonpath,
                    "PYTHONIOENCODING": "utf-8",
                },
            ),
            timeout=timeout,
        )

        stdout_bytes, _ = await proc.communicate()
        output_text = stdout_bytes.decode("utf-8", errors="replace")
        returncode = proc.returncode

        known_tests = _extract_test_case_names(test_code)
        result = _parse_pytest_output(output_text, known_tests)

        if returncode != 0 and not result.all_passed:
            if result.errors == 0 and result.failed == 0:
                result.errors = 1
                result.total = max(result.total, 1)
            if result.summary.startswith("✅") or not result.summary.strip():
                result.summary = _build_failure_summary(output_text, returncode)

        if not result.has_results:
            result.execution_mode = "pytest"
            result.total = max(result.total, len(known_tests) or 1)
            result.failed = max(result.failed, 1)
            result.summary = _build_failure_summary(output_text, returncode)
            result.execution_output = output_text[:3000]

        await _log(
            "SUCCESS" if result.all_passed else "WARN",
            f"[测试执行] {result.summary.splitlines()[0][:200]}",
        )

    except TimeoutError:
        await _log("ERROR", f"[测试执行] 超时 ({timeout}s)")
        result = TestResult(
            execution_mode="pytest",
            total=len(_extract_test_case_names(test_code)) or 1,
            failed=len(_extract_test_case_names(test_code)) or 1,
            summary=f"测试执行超时 (>{timeout}s)",
        )

    except Exception as exc:
        await _log("ERROR", f"[测试执行] 异常: {exc}")
        known = _extract_test_case_names(test_code)
        result = TestResult(
            execution_mode="pytest",
            total=len(known) or 1,
            failed=len(known) or 1,
            summary=f"测试执行异常: {str(exc)}",
        )

    # 补充失败的用例（如果 pytest 因导入错误未产生用例结果）
    if result.failed > 0 and not result.cases:
        for name in _extract_test_case_names(test_code):
            result.cases.append(TestCaseResult(
                name=name, passed=False,
                error_message=result.execution_output[:200],
            ))

    return result


async def run_integration_tests(
    integration_test_code: str,
    project_id: str,
    module_files: dict[str, str] | None = None,  # {filename: code}
    timeout: int = 60,
    log_callback=None,
) -> TestResult:
    """在集成环境中执行集成测试。

    Args:
        integration_test_code: 集成测试代码
        project_id: 项目 ID
        module_files: 模块文件 {文件名: 代码}，会被写入沙箱供导入
        timeout: 超时秒数
        log_callback: async fn(level, message, module_name)

    Returns:
        TestResult
    """
    if not integration_test_code or not integration_test_code.strip():
        return TestResult(
            execution_mode="skipped",
            summary="无集成测试代码，跳过执行",
        )

    async def _log(level: str, msg: str) -> None:
        if log_callback:
            await log_callback(level, msg, "integration_test")

    if not _is_pytest_available():
        await _log("WARN", "[集成测试] pytest 不可用，降级为语法检查")
        return _check_syntax(integration_test_code, "test_integration.py")

    # 创建集成测试沙箱
    sandbox = Path(tempfile.gettempdir()) / "devflow_test_env" / project_id / "integration"
    sandbox.mkdir(parents=True, exist_ok=True)

    # 写入测试文件
    test_file = sandbox / "test_integration.py"
    test_file.write_text(integration_test_code, encoding="utf-8")

    # 写入模块依赖文件
    if module_files:
        for fname, fcode in module_files.items():
            (sandbox / fname).write_text(fcode, encoding="utf-8")

    # conftest
    (sandbox / "conftest.py").touch()

    # 安装依赖（如果需要）
    req_file = sandbox / "requirements.txt"
    if req_file.exists():
        try:
            proc = await asyncio.wait_for(
                asyncio.create_subprocess_exec(
                    sys.executable, "-m", "pip", "install", "-r", str(req_file),
                    "--quiet", "--target", str(sandbox / ".deps"),
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                ),
                timeout=30,
            )
            await proc.communicate()
        except Exception as exc:
            logger.warning("pip install -r 失败: %s", exc)
            pass

    await _log("INFO", "[集成测试] 运行集成测试...")

    try:
        proc = await asyncio.wait_for(
            asyncio.create_subprocess_exec(
                sys.executable, "-m", "pytest",
                str(test_file), "-v", "--tb=short", "--color=no",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=str(sandbox),
                env={**os.environ, "PYTHONPATH": str(sandbox), "PYTHONIOENCODING": "utf-8"},
            ),
            timeout=timeout,
        )
        stdout_bytes, _ = await proc.communicate()
        output_text = stdout_bytes.decode("utf-8", errors="replace")
        result = _parse_pytest_output(output_text, _extract_test_case_names(integration_test_code))
        await _log("SUCCESS" if result.all_passed else "WARN", f"[集成测试] {result.summary}")
        return result

    except TimeoutError:
        await _log("ERROR", f"[集成测试] 超时 ({timeout}s)")
        return TestResult(
            execution_mode="pytest", total=1, failed=1,
            summary=f"集成测试执行超时 (>{timeout}s)",
        )
    except Exception as exc:
        await _log("ERROR", f"[集成测试] 异常: {exc}")
        return TestResult(
            execution_mode="pytest", total=1, failed=1,
            summary=f"集成测试异常: {str(exc)}",
        )


async def run_full_test_suite(
    project_id: str,
    sandbox_dir: Path,
    timeout: int = 120,
    log_callback=None,
) -> TestResult:
    """交付前全量测试：运行沙箱中所有 *_test.py 文件。

    Args:
        project_id: 项目 ID
        sandbox_dir: 包含所有模块文件的沙箱目录
        timeout: 超时秒数
        log_callback: async fn(level, message, module_name)

    Returns:
        TestResult
    """
    async def _log(level: str, msg: str) -> None:
        if log_callback:
            await log_callback(level, msg, "full_test")

    if not sandbox_dir.exists():
        return TestResult(execution_mode="skipped", summary="沙箱目录不存在")

    test_files = list(sandbox_dir.glob("test_*.py"))
    if not test_files:
        # 生成一个空测试结果
        return TestResult(
            execution_mode="skipped",
            summary="未找到任何测试文件",
        )

    if not _is_pytest_available():
        await _log("WARN", "[全量测试] pytest 不可用，降级为语法检查")
        combined = TestResult(execution_mode="syntax_check", total=0)
        for tf in test_files:
            code = tf.read_text(encoding="utf-8", errors="ignore")
            partial = _check_syntax(code, tf.name)
            combined.total += partial.total
            combined.passed += partial.passed
            combined.failed += partial.failed
            combined.errors += partial.errors
            combined.cases.extend(partial.cases)
        combined.summary = f"语法检查: {combined.passed}/{combined.total} 通过 (降级方案)"
        return combined

    await _log("INFO", f"[全量测试] 运行 {len(test_files)} 个测试文件...")

    # 写入 conftest
    (sandbox_dir / "conftest.py").touch()

    try:
        proc = await asyncio.wait_for(
            asyncio.create_subprocess_exec(
                sys.executable, "-m", "pytest",
                *(str(tf) for tf in test_files),
                "-v", "--tb=short", "--color=no",
                "--json-report", "--json-report-file=test_report.json",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=str(sandbox_dir),
                env={**os.environ, "PYTHONPATH": str(sandbox_dir), "PYTHONIOENCODING": "utf-8"},
            ),
            timeout=timeout,
        )
        stdout_bytes, _ = await proc.communicate()
        output_text = stdout_bytes.decode("utf-8", errors="replace")

        all_known_tests: list[str] = []
        for tf in test_files:
            all_known_tests.extend(_extract_test_case_names(
                tf.read_text(encoding="utf-8", errors="ignore")
            ))
        result = _parse_pytest_output(output_text, all_known_tests)

        # 尝试读取 JSON 报告
        json_report = sandbox_dir / "test_report.json"
        if json_report.exists():
            result.test_report_path = str(json_report)

        await _log(
            "SUCCESS" if result.all_passed else "WARN",
            f"[全量测试] {result.summary}",
        )
        return result

    except TimeoutError:
        await _log("ERROR", f"[全量测试] 超时 ({timeout}s)")
        return TestResult(
            execution_mode="pytest", total=len(test_files), failed=len(test_files),
            summary=f"全量测试执行超时 (>{timeout}s)",
        )
    except Exception as exc:
        await _log("ERROR", f"[全量测试] 异常: {exc}")
        return TestResult(
            execution_mode="pytest", total=len(test_files), failed=len(test_files),
            summary=f"全量测试异常: {str(exc)}",
        )
