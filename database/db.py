"""数据库连接与会话管理。

使用 SQLAlchemy 2.0 异步引擎 + aiosqlite 实现，
提供依赖注入式的数据库会话获取。
"""

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import text

from config import DATABASE_URL

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
    """创建所有数据库表（首次启动时调用）。

    同时尝试为新列做轻量迁移，避免因模型新增字段而报错。
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # ── 轻量迁移：尝试添加可能缺失的新列 ──
    async with engine.begin() as conn:
        # alignment_json (v0.4.0 新增)
        try:
            await conn.execute(
                text("ALTER TABLE projects ADD COLUMN alignment_json TEXT")
            )
        except Exception:
            pass  # 列已存在（SQLite 不支持 IF NOT EXISTS）

    # ── FixSession 表迁移 (v0.6.0 新增：闭环修复记录) ──
    try:
        from database.models import Base as _Base
        async with engine.begin() as conn:
            await conn.run_sync(_Base.metadata.create_all)
    except Exception:
        pass
