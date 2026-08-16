"""NoneBot 桥接管理 API 路由 — 状态 / 环境 / 适配器 / 插件 / 商店 / 配置 / 日志。

模块导入时注册 AI 工具全集（Web 服务随应用启动，单元测试不导入本模块，
不污染全局注册表）。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Query
from pydantic import BaseModel

from channels.nonebot_bridge.tools import register_nonebot_tools
from services.nonebot import NoneBotService
from web.routers._errors import server_error

register_nonebot_tools()

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
    """适配器安装载荷（source 可选：git 源 / 本地路径，空 = PyPI 包名）。"""

    key: str
    enable: bool = True
    source: str = ""


class EnablePayload(BaseModel):
    """启用/停用载荷（适配器 key 或插件 module）。"""

    key: str = ""
    module: str = ""
    enabled: bool


class UpgradeEnvPayload(BaseModel):
    """环境升级载荷（packages 缺省升级 NoneBot 基线）。"""

    packages: Optional[List[str]] = None


class InstallPluginPayload(BaseModel):
    """插件安装载荷（source 可选：git 源 / 本地路径；editable 本地可编辑安装）。"""

    module_name: str
    source: str = ""
    editable: bool = False


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
        return {"ready": True, **await _service.get_status()}
    except Exception as exc:  # noqa: BLE001
        return {"ready": False, "error": str(exc)}


@router.post("/restart")
async def restart_worker() -> Dict[str, Any]:
    """重启 NoneBot worker 子进程。"""
    return await _service.restart()


@router.post("/worker/start")
async def start_worker() -> Dict[str, Any]:
    """启动 worker 子进程（频道须已启用）。"""
    return await _service.start_worker()


@router.post("/worker/stop")
async def stop_worker() -> Dict[str, Any]:
    """停止 worker 子进程（可随时再启动）。"""
    return await _service.stop_worker()


# ------------------------------------------------------------------
# 环境管理（uv / venv / 包）
# ------------------------------------------------------------------


@router.get("/env")
async def env_status() -> Dict[str, Any]:
    """环境详情：uv / Python 版本、venv 就绪态、基线包、安装进度。"""
    return await _service.get_env_status()


@router.post("/env/bootstrap")
async def bootstrap_env() -> Dict[str, Any]:
    """初始化 worker venv（不依赖频道启用）。"""
    return await _service.bootstrap_env()


@router.post("/env/upgrade")
async def upgrade_env(payload: UpgradeEnvPayload) -> Dict[str, Any]:
    """升级包（packages 缺省升级 NoneBot 基线）。"""
    return await _service.upgrade_env(payload.packages)


@router.post("/env/rebuild")
async def rebuild_env() -> Dict[str, Any]:
    """删除并重建 worker venv（运行中先停止，重建后恢复）。"""
    return await _service.rebuild_env()


@router.get("/env/packages")
async def list_packages() -> Dict[str, Any]:
    """列出 worker venv 已安装包。"""
    return await _service.list_packages()


@router.get("/env/sources")
async def env_sources() -> Dict[str, Any]:
    """git/本地源安装项清单（溯源记录 + 本地检出目录）。"""
    return _service.get_sources_status()


@router.post("/env/resync")
async def resync_sources() -> Dict[str, Any]:
    """按溯源记录拉取并重装全部 git 源安装项。"""
    return await _service.resync_sources()


# ------------------------------------------------------------------
# 适配器
# ------------------------------------------------------------------


@router.get("/adapters")
async def list_adapters() -> Dict[str, Any]:
    """列出内置 ∪ 注册表适配器（含安装/启用状态与平台接入元数据）。

    先确保注册表已加载（限时等待，超时后台继续），否则冷启动只会看到
    内置适配器 —— 注册表社区适配器（钉钉/GitHub/B站等 20+）会缺席。
    """
    import asyncio

    await _service.ensure_adapters_loaded()
    adapters = await asyncio.to_thread(_service.list_adapters)
    return {"adapters": adapters}


@router.post("/adapters/install")
async def install_adapter(payload: InstallAdapterPayload) -> Dict[str, Any]:
    """安装适配器包到 worker venv（可选同时加入启用列表；支持 git 源/本地路径）。"""
    return await _service.install_adapter(
        payload.key, enable=payload.enable, source=payload.source
    )


@router.post("/adapters/uninstall")
async def uninstall_adapter(payload: InstallAdapterPayload) -> Dict[str, Any]:
    """卸载适配器包并从启用列表移除。"""
    return await _service.uninstall_adapter(payload.key)


@router.post("/adapters/enable")
async def set_adapter_enabled(payload: EnablePayload) -> Dict[str, Any]:
    """启用/停用适配器（仅调整加载列表，不动包）。"""
    return _service.set_adapter_enabled(payload.key, payload.enabled)


# ------------------------------------------------------------------
# 插件
# ------------------------------------------------------------------


@router.get("/plugins")
async def list_plugins() -> Dict[str, Any]:
    """列出 worker 中已加载的插件（实时）。"""
    return await _service.list_plugins()


@router.post("/plugins/install")
async def install_plugin(payload: InstallPluginPayload) -> Dict[str, Any]:
    """安装插件到 worker venv 并热重启（商店 / git 源 / 本地路径可编辑）。"""
    return await _service.install_plugin(
        payload.module_name, source=payload.source, editable=payload.editable
    )


@router.post("/plugins/uninstall")
async def uninstall_plugin(payload: InstallPluginPayload) -> Dict[str, Any]:
    """卸载插件并热重启生效。"""
    return await _service.uninstall_plugin(payload.module_name)


@router.post("/plugins/enable")
async def set_plugin_enabled(payload: EnablePayload) -> Dict[str, Any]:
    """启用/停用插件（仅调整加载列表，保留已安装包）。"""
    return _service.set_plugin_enabled(payload.module, payload.enabled)


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
