"""AI 桌面 Web API — 组件清单 / 注入预览 / 手动刷新 / 天气地区检索。

配置读写（启停开关、刷新间隔等）复用通用实体配置端点
``PUT /api/entities/ai_desktop/config``，本路由只提供组件专属操作。
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException

from . import framework
from .modules.weather import search_locations


def build_router() -> APIRouter:
    """构建 AI 桌面路由（被 web 层挂载到 /api/entity/ai_desktop）。"""
    router = APIRouter()

    @router.get("/modules")
    async def list_modules() -> dict:
        """全部组件的状态与配置描述（面板数据驱动渲染）。"""
        modules = [m.describe() for m in framework.all_modules()]
        return {"modules": modules, "count": len(modules)}

    @router.get("/preview")
    async def preview_context() -> dict:
        """当前注入 AI 上下文的聚合文本预览。"""
        content = framework.render_context()
        return {"injecting": bool(content), "content": content}

    @router.post("/modules/{key}/refresh")
    async def refresh_module(key: str) -> dict:
        """立即刷新指定轮询型组件（忽略刷新间隔）。"""
        module = framework.get_module(key)
        if module is None:
            raise HTTPException(status_code=404, detail=f"未找到组件: {key}")
        if module.refresh_interval <= 0:
            raise HTTPException(status_code=400, detail="即时型组件无需手动刷新")
        success = await framework.force_refresh(key)
        return {
            "success": success,
            "last_error": module.last_error or None,
            "content": module.render(),
        }

    @router.get("/modules/weather/geocode")
    async def geocode_locations(query: str = "") -> dict:
        """检索天气地区候选（面板搜索确认用；名称/行政区/国家/经纬度）。"""
        query = query.strip()
        if not query:
            return {"candidates": []}
        try:
            candidates = await asyncio.to_thread(search_locations, query)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"地区检索失败: {exc}") from exc
        return {"candidates": candidates}

    return router
