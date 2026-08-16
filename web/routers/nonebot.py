"""NoneBot 桥接管理 API 路由 — 状态 / 适配器 / 插件 / 商店 / 配置 / 日志。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Query
from pydantic import BaseModel

from services.nonebot import NoneBotService
from web.routers._errors import server_error

router = APIRouter(prefix="/nonebot", tags=["nonebot"])

_service = NoneBotService()


class ConfigPayload(BaseModel):
    """桥接配置保存载荷（enabled 不经此路径，走频道启停 API）。"""

    adapters: Optional[List[str]] = None
    plugins: Optional[List[str]] = None
    nonebot_env: Optional[Dict[str, Any]] = None
    intercept_all: Optional[bool] = None
    bridge_ws_port: Optional[int] = None
    worker_host: Optional[str] = None
    worker_port: Optional[int] = None
    auto_restart: Optional[bool] = None


class InstallAdapterPayload(BaseModel):
    """适配器安装载荷。"""

    key: str
    enable: bool = True


class InstallPluginPayload(BaseModel):
    """插件安装载荷。"""

    module_name: str


class RunCommandPayload(BaseModel):
    """插件命令触发载荷。"""

    command: str
    bot_id: str = ""
    adapter: str = ""


# ------------------------------------------------------------------
# 状态与重启
# ------------------------------------------------------------------


@router.get("/status")
async def nonebot_status() -> Dict[str, Any]:
    """获取 NoneBot 桥接全景状态（频道 / worker 进程 / 安装进度）。"""
    try:
        return {"ready": True, **_service.get_status()}
    except Exception as exc:  # noqa: BLE001
        return {"ready": False, "error": str(exc)}


@router.post("/restart")
async def restart_worker() -> Dict[str, Any]:
    """重启 NoneBot worker 子进程。"""
    return await _service.restart()


# ------------------------------------------------------------------
# 适配器
# ------------------------------------------------------------------


@router.get("/adapters")
async def list_adapters() -> Dict[str, Any]:
    """列出内置 ∪ 注册表适配器（含安装/启用状态与平台接入元数据）。"""
    import asyncio

    adapters = await asyncio.to_thread(_service.list_adapters)
    return {"adapters": adapters}


@router.post("/adapters/install")
async def install_adapter(payload: InstallAdapterPayload) -> Dict[str, Any]:
    """安装适配器包到 worker venv（可选同时加入启用列表）。"""
    return await _service.install_adapter(payload.key, enable=payload.enable)


@router.post("/adapters/uninstall")
async def uninstall_adapter(payload: InstallAdapterPayload) -> Dict[str, Any]:
    """卸载适配器包并从启用列表移除。"""
    return await _service.uninstall_adapter(payload.key)


# ------------------------------------------------------------------
# 插件
# ------------------------------------------------------------------


@router.get("/plugins")
async def list_plugins() -> Dict[str, Any]:
    """列出 worker 中已加载的插件（实时）。"""
    return await _service.list_plugins()


@router.post("/plugins/install")
async def install_plugin(payload: InstallPluginPayload) -> Dict[str, Any]:
    """从商店安装插件到 worker venv 并热重启生效。"""
    return await _service.install_plugin(payload.module_name)


@router.post("/plugins/uninstall")
async def uninstall_plugin(payload: InstallPluginPayload) -> Dict[str, Any]:
    """卸载插件并热重启生效。"""
    return await _service.uninstall_plugin(payload.module_name)


# ------------------------------------------------------------------
# 商店
# ------------------------------------------------------------------


@router.get("/store/plugins")
async def store_plugins(
    query: str = Query(default="", description="关键词过滤"),
    limit: int = Query(default=60, ge=1, le=300),
) -> Dict[str, Any]:
    """浏览 / 搜索 NoneBot 插件商店（registry.nonebot.dev 代理）。"""
    plugins = await _service.fetch_store_plugins()
    if query.strip():
        plugins = await _service.search_store_plugins(query, limit=limit)
    else:
        plugins = plugins[:limit]
    return {"count": len(plugins), "plugins": plugins}


@router.get("/store/adapters")
async def store_adapters() -> Dict[str, Any]:
    """浏览 NoneBot 适配器注册表。"""
    adapters = await _service.fetch_store_adapters()
    return {"count": len(adapters), "adapters": adapters}


# ------------------------------------------------------------------
# 配置 / 日志 / 命令
# ------------------------------------------------------------------


@router.get("/config")
async def get_bridge_config() -> Dict[str, Any]:
    """获取 NoneBot 桥接频道的配置。"""
    return _service.get_config()


@router.put("/config")
async def save_bridge_config(config: ConfigPayload) -> Dict[str, Any]:
    """保存桥接配置（worker 相关变更自动热重启）。"""
    patch = {k: v for k, v in config.model_dump().items() if v is not None}
    return await _service.save_config(patch)


@router.get("/logs")
async def tail_logs(count: int = Query(default=200, ge=1, le=2000)) -> Dict[str, Any]:
    """读取 worker 日志环尾部。"""
    return {"logs": _service.tail_logs(count)}


@router.post("/command")
async def run_command(payload: RunCommandPayload) -> Dict[str, Any]:
    """以虚拟用户身份触发 NoneBot 插件命令并捕获回复（调试用）。"""
    try:
        return await _service.run_command(
            payload.command, bot_id=payload.bot_id, adapter=payload.adapter
        )
    except Exception as exc:  # noqa: BLE001
        raise server_error("执行 NoneBot 命令", exc) from exc
