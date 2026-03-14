"""
ExtRouter：FastAPI 路由注册器。

允许外部插件向 webhook 适配器注册自定义 API 路由，
无需直接修改 webhook.py。

用法::

    from agent.ext import ExtRouter

    ext = ExtRouter(prefix="/my_plugin")

    @ext.get("/status")
    async def status():
        return {"ok": True}

    # 在 webhook app 启动时：
    ext_router.mount_all(fastapi_app)
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, FastAPI

from core.log import log


class ExtRouter:
    """单个扩展路由组。"""

    def __init__(self, prefix: str = "", tags: Optional[List[str]] = None) -> None:
        self.router = APIRouter(prefix=prefix, tags=tags or [])

    # 快捷装饰器代理
    def get(self, path: str, **kw: Any) -> Callable:
        return self.router.get(path, **kw)

    def post(self, path: str, **kw: Any) -> Callable:
        return self.router.post(path, **kw)

    def put(self, path: str, **kw: Any) -> Callable:
        return self.router.put(path, **kw)

    def delete(self, path: str, **kw: Any) -> Callable:
        return self.router.delete(path, **kw)


# ------------------------------------------------------------------
# 全局注册表
# ------------------------------------------------------------------

_ext_routers: List[ExtRouter] = []


def register_ext_router(ext: ExtRouter) -> None:
    """注册一个扩展路由。"""
    _ext_routers.append(ext)
    log(f"扩展路由已注册: prefix={ext.router.prefix}", "DEBUG")


def mount_all(app: FastAPI) -> None:
    """将所有已注册的扩展路由挂载到 FastAPI 应用。"""
    for ext in _ext_routers:
        app.include_router(ext.router)
    log(f"已挂载 {len(_ext_routers)} 个扩展路由")
