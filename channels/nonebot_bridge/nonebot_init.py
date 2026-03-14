"""NoneBot 初始化与生命周期管理。

在 AnelfTools 启动时初始化 NoneBot，注册配置中指定的适配器，
并将 NoneBot 的 ASGI 路由挂载到现有 FastAPI 应用。
"""

from __future__ import annotations

import importlib
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from core.log import log

from .config import KNOWN_ADAPTERS

if TYPE_CHECKING:
    from fastapi import FastAPI

_nonebot_initialized: bool = False
_registered_adapter_names: List[str] = []


def is_initialized() -> bool:
    return _nonebot_initialized


def get_registered_adapters() -> List[str]:
    return list(_registered_adapter_names)


def init_nonebot(adapter_names: List[str], env_config: Optional[Dict[str, Any]] = None) -> bool:
    """初始化 NoneBot 并注册指定的适配器。

    Args:
        adapter_names: 要注册的适配器名称列表（对应 KNOWN_ADAPTERS 的 key）
        env_config: 传递给 nonebot.init() 的额外配置

    Returns:
        是否初始化成功
    """
    global _nonebot_initialized, _registered_adapter_names

    if _nonebot_initialized:
        log("NoneBot 已初始化，跳过重复初始化", "WARNING")
        return True

    if not adapter_names:
        log("NoneBot Bridge: 未配置任何适配器，跳过初始化", "DEBUG")
        return False

    try:
        import nonebot
    except ImportError:
        log("NoneBot Bridge: nonebot2 未安装，无法初始化", "ERROR")
        return False

    init_kwargs: Dict[str, Any] = {}
    if env_config:
        init_kwargs.update(env_config)

    try:
        nonebot.init(**init_kwargs)
        driver = nonebot.get_driver()
    except Exception as exc:
        log(f"NoneBot Bridge: nonebot.init() 失败 - {exc}", "ERROR")
        return False

    registered: List[str] = []
    for name in adapter_names:
        adapter_cls = _load_adapter_class(name)
        if adapter_cls is None:
            continue
        try:
            driver.register_adapter(adapter_cls)
            registered.append(name)
            log(f"NoneBot Bridge: 适配器已注册 - {name}")
        except Exception as exc:
            log(f"NoneBot Bridge: 适配器注册失败 - {name}: {exc}", "WARNING")

    if not registered:
        log("NoneBot Bridge: 没有成功注册任何适配器", "WARNING")
        return False

    _registered_adapter_names = registered
    _nonebot_initialized = True
    log(f"NoneBot Bridge: 初始化完成，已注册 {len(registered)} 个适配器: {', '.join(registered)}")
    return True


def mount_nonebot_app(fastapi_app: "FastAPI") -> None:
    """将 NoneBot 的 ASGI 应用挂载到现有 FastAPI。"""
    if not _nonebot_initialized:
        return

    try:
        import nonebot
        nb_app = nonebot.get_app()
        if nb_app is not None and nb_app is not fastapi_app:
            fastapi_app.mount("/nonebot", nb_app)
            log("NoneBot Bridge: ASGI 应用已挂载到 /nonebot")
    except Exception as exc:
        log(f"NoneBot Bridge: ASGI 挂载失败 - {exc}", "WARNING")


def get_nonebot_bots() -> Dict[str, Any]:
    """获取当前在线的 NoneBot Bot 实例。"""
    if not _nonebot_initialized:
        return {}
    try:
        import nonebot
        return dict(nonebot.get_bots())
    except Exception as e:
        log(f"NoneBot 获取 Bot 列表失败: {e}", "DEBUG")
        return {}


def get_nonebot_status() -> Dict[str, Any]:
    """获取 NoneBot 运行状态摘要。"""
    bots = get_nonebot_bots()
    return {
        "initialized": _nonebot_initialized,
        "registered_adapters": list(_registered_adapter_names),
        "online_bots": [
            {
                "self_id": bot_id,
                "adapter": type(bot.adapter).__name__ if hasattr(bot, "adapter") else "unknown",
            }
            for bot_id, bot in bots.items()
        ],
    }


def _load_adapter_class(name: str) -> Optional[type]:
    """动态加载 NoneBot 适配器类。"""
    info = KNOWN_ADAPTERS.get(name)
    if info is None:
        log(f"NoneBot Bridge: 未知适配器 '{name}'，尝试直接导入 nonebot.adapters.{name}", "WARNING")
        try:
            mod = importlib.import_module(f"nonebot.adapters.{name}")
            return getattr(mod, "Adapter", None)
        except ImportError:
            log(f"NoneBot Bridge: 适配器包 nonebot.adapters.{name} 未安装", "ERROR")
            return None

    module_path = info["import"]
    class_name = info["class"]
    try:
        mod = importlib.import_module(module_path)
        return getattr(mod, class_name)
    except ImportError:
        log(
            f"NoneBot Bridge: 适配器 '{name}' 的包 '{info['package']}' 未安装，"
            f"请运行: pip install {info['package']}",
            "ERROR",
        )
        return None
    except AttributeError:
        log(f"NoneBot Bridge: 模块 '{module_path}' 中未找到类 '{class_name}'", "ERROR")
        return None
