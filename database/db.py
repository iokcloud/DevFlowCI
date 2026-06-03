"""数据库连接与会话管理。

使用 SQLAlchemy 2.0 异步引擎 + aiosqlite 实现，
提供依赖注入式的数据库会话获取。
"""

import logging

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from config import DATABASE_URL

logger = logging.getLogger(__name__)

# ── 异步引擎 ──────────────────────────────────────────────
engine = create_async_engine(
    DATABASE_URL,
    echo=False,                     # 生产环境建议关闭 SQL 日志
    connect_args={"check_same_thread": False},  # SQLite 多线程支持
)

# ── 会话工厂 ──────────────────────────────────────────────
async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


# ── 声明式基类 ────────────────────────────────────────────
class Base(DeclarativeBase):
    """所有 ORM 模型的基类。"""
    pass


async def get_db() -> AsyncSession:  # type: ignore[misc]
    """FastAPI 依赖注入：获取数据库会话。

    用法:
        @app.get("/")
        async def root(db: AsyncSession = Depends(get_db)):
            ...
    """
    async with async_session_factory() as session:
        try:
            yield session
        finally:
            await session.close()


async def init_db() -> None:
    """初始化数据库 — 优先使用 Alembic 迁移，首次启动时回退到 create_all。

    迁移策略：
    1. 优先执行 `alembic upgrade head`（所有 schema 变更通过迁移管理）
    2. 如果 alembic 未初始化（全新部署），回退到 create_all
    """
    import asyncio
    from pathlib import Path

    # 检查是否已有 alembic 版本表（表示之前已运行过迁移）
    db_path = Path(str(engine.url).replace("sqlite+aiosqlite:///", ""))
    if not db_path.is_absolute():
        db_path = Path.cwd() / db_path

    try:
        # 尝试运行 Alembic 迁移
        from alembic.config import Config

        from alembic import command

        alembic_ini = Path(__file__).parent.parent / "alembic.ini"
        if alembic_ini.exists() and db_path.exists():
            alembic_cfg = Config(str(alembic_ini))
            # ★ command.upgrade() 内部会调用 asyncio.run()，而 init_db() 本身
            #    在异步上下文中运行，直接调用会导致嵌套事件循环冲突。
            #    通过 to_thread 放到独立线程执行，避免冲突。
            await asyncio.to_thread(command.upgrade, alembic_cfg, "head")
            return
    except Exception as exc:
        logger.warning(
            "Alembic 迁移不可用或执行失败，回退到 create_all: %s", exc
        )

    # 回退：使用 create_all（首次部署或 Alembic 不可用）
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
