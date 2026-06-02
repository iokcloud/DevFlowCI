"""占位文件生成 — blocked 模块的骨架代码。

从 workflow.executor 中独立出来。
"""

from __future__ import annotations

from typing import Any

from workflow.langgraph_def import WorkflowState

# ── 占位文件生成 ──────────────────────────────────────────

# ── 占位文件生成 ──────────────────────────────────────────

def _generate_stub_code(module_name: str, module_type: str, description: str, failure_reason: str) -> str:
    """为 blocked 模块生成骨架占位代码。

    Args:
        module_name: 模块名称
        module_type: 模块类型 (backend/frontend/database/integration/testing)
        description: 模块描述
        failure_reason: 失败/阻塞原因

    Returns:
        带详细注释的骨架代码字符串
    """
    header = f'''"""
⚠️ 此模块因以下原因被自动阻塞，请手动完成实现。

模块名称: {module_name}
模块描述: {description}
模块类型: {module_type}
阻塞原因: {failure_reason}

原始需求: 请根据项目规划上下文手动完善此模块。
建议: 先阅读 TODO.md 了解项目整体状态，再对此文件进行开发。
"""
'''

    if module_type in ("backend", "integration"):
        stub = f'''{header}
# TODO: 实现以下函数/类的完整逻辑

def {module_name}() -> dict:
    """待实现：{description}

    Returns:
        dict: 操作结果
    """
    raise NotImplementedError(
        "此模块已被自动阻塞，请手动实现。"
        "详见文件顶部注释和项目根目录的 TODO.md。"
    )


if __name__ == "__main__":
    print("此模块尚未完成，请参考 TODO.md 进行开发。")
'''
    elif module_type == "database":
        stub = f'''{header}
# TODO: 定义数据库模型

# from sqlalchemy import Column, Integer, String
# from database.db import Base
#
# class PlaceholderModel(Base):
#     __tablename__ = "{module_name}"
#     id = Column(Integer, primary_key=True)
#     # 请在此添加字段定义

print("此模块尚未完成，请参考 TODO.md 进行开发。")
'''
    elif module_type == "frontend":
        stub = f'''{header}
<!-- TODO: 实现前端组件 -->
<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"><title>{module_name}</title></head>
<body>
  <h1>模块: {module_name}</h1>
  <p>此模块尚未完成，请参考 TODO.md 进行开发。</p>
</body>
</html>
'''
    else:
        stub = f'''{header}
# TODO: {description}

# 此模块尚未完成，阻塞原因: {failure_reason}
# 请参考项目根目录的 TODO.md 完成开发。
'''
    return stub


