# 编码规范

> 最后更新：2026-05-31 | 版本：v1.0.0

## 通用原则

1. **可读性优先**：代码是写给人看的，顺便给机器运行。
2. **显式优于隐式**：宁可多写一行也不让读者猜。
3. **一致性**：代码风格在整个项目中保持一致。

## Python 规范

### 命名约定
- 模块/文件：`snake_case.py`
- 类：`PascalCase`
- 函数/方法：`snake_case`
- 变量：`snake_case`
- 常量：`UPPER_SNAKE_CASE`
- 私有成员：前缀 `_`

### 类型注解
- 所有公共函数/方法必须有完整类型注解（兼容 mypy strict）
- 使用 `from __future__ import annotations` 推迟求值
- `dict[str, Any]` 优于 `Dict[str, Any]`

### Docstring
- 使用 Google 风格
- 公共函数/类必须包含：简短描述、Args、Returns、Raises（如有）
- 示例：
```python
def analyze(requirement: str, context: str = "") -> tuple[dict, list]:
    """执行需求对齐分析。

    Args:
        requirement: 用户原始需求
        context: 项目上下文 JSON 字符串

    Returns:
        (对齐结果 JSON, 验证错误列表)

    Raises:
        ValueError: LLM 回复无法解析为 JSON 时
    """
```

### 格式化
- `black` 默认配置（行宽 88）
- `isort` 导入排序
- 单行不超过 120 字符（宽松于 black 的 88）

### 异常处理
- 禁止裸 `except:` 或 `except Exception:` 吞异常
- 外部调用（HTTP、文件、DB）必须 try/except
- 异常必须记录日志（至少 `push_log` 或 `logger.error`）

### 异步
- 使用 `async/await` 而非回调
- `asyncio.create_task()` 创建后台任务，不阻塞主循环
- 协程命名以 `async def` 显式声明

## JavaScript 规范（前端）

### 命名
- 函数：`camelCase`
- 常量：`UPPER_SNAKE_CASE`
- DOM 引用：`$` 前缀或描述性变量名

### 风格
- 使用 `const` / `let`，禁止 `var`
- 模板字符串优先于字符串拼接
- 箭头函数用于回调

### 安全
- 用户输入必须 `escapeHtml()` 后再插入 DOM
- 不使用 `innerHTML` 插入不可信内容

## 通用安全规范

1. **绝不硬编码密钥/Token/密码**：使用环境变量（`.env` 文件）
2. **用户输入校验**：所有来自外部的输入必须校验
3. **路径遍历防护**：文件操作使用 `Path` 对象，限制访问范围

## Git 提交规范

- 格式：`<type>: <简短描述>`
- type: feat / fix / docs / refactor / test / chore
- 示例：`feat: 新增需求对齐阶段` / `fix: 修复历史项目重复显示`
