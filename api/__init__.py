"""API 路由注册入口 — 统一注册所有子路由到 FastAPI 应用。"""

from __future__ import annotations

from fastapi import FastAPI


def register_routers(app: FastAPI) -> None:
    """将所有 API 子路由注册到 FastAPI 应用（惰性加载，缺失模块不阻断）。"""
    _try_include(app, "api.health")
    _try_include(app, "api.fs_browser")
    _try_include(app, "api.maintenance")
    _try_include(app, "api.projects")
    _try_include(app, "api.workflow_api")
    _try_include(app, "api.delivery")
    _try_include(app, "api.streaming")
    _try_include(app, "api.feedback")


def _try_include(app: FastAPI, module_name: str) -> None:
    """尝试加载并注册路由模块，缺失时静默跳过。"""
    import importlib
    try:
        mod = importlib.import_module(module_name)
        if hasattr(mod, "router"):
            app.include_router(mod.router)
    except ModuleNotFoundError:
        pass
