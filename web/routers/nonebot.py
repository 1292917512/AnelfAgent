"""NoneBot 桥接管理 API 路由。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/nonebot", tags=["nonebot"])


@router.get("/status")
async def nonebot_status() -> Dict[str, Any]:
    """获取 NoneBot 桥接的运行状态。"""
    try:
        from channels.nonebot_bridge.nonebot_init import get_nonebot_status
        return {"ready": True, **get_nonebot_status()}
    except ImportError:
        return {"ready": False, "initialized": False, "registered_adapters": [], "online_bots": []}


@router.get("/adapters")
async def list_known_adapters() -> Dict[str, Any]:
    """列出所有已知的 NoneBot 适配器及其安装状态。"""
    from channels.nonebot_bridge.config import KNOWN_ADAPTERS
    from channels.nonebot_bridge.nonebot_init import get_registered_adapters

    registered = set(get_registered_adapters())
    adapters: List[Dict[str, Any]] = []

    for key, info in KNOWN_ADAPTERS.items():
        installed = _check_adapter_installed(info["import"])
        adapters.append({
            "key": key,
            "label": info["label"],
            "package": info["package"],
            "installed": installed,
            "registered": key in registered,
        })

    return {"adapters": adapters}


@router.get("/bots")
async def list_bots() -> Dict[str, Any]:
    """列出当前在线的 NoneBot Bot 实例。"""
    try:
        from channels.nonebot_bridge.nonebot_init import get_nonebot_bots
        bots = get_nonebot_bots()
        bot_list: List[Dict[str, str]] = []
        for bot_id, bot in bots.items():
            adapter_name = "unknown"
            adapter = getattr(bot, "adapter", None)
            if adapter is not None:
                try:
                    adapter_name = type(adapter).get_name()
                except (AttributeError, TypeError):
                    adapter_name = type(adapter).__name__
            bot_list.append({
                "self_id": bot_id,
                "adapter": adapter_name,
            })
        return {"bots": bot_list}
    except ImportError:
        return {"bots": []}


@router.get("/config")
async def get_bridge_config() -> Dict[str, Any]:
    """获取 NoneBot 桥接频道的配置。"""
    import json
    from pathlib import Path

    cfg_file = Path("channels/nonebot_bridge/channel_config.json")
    if cfg_file.exists():
        try:
            return json.loads(cfg_file.read_text("utf-8"))
        except Exception as e:
            from core.log import log
            log(f"NoneBot 桥接配置加载失败: {e}", "DEBUG")
    return {"enabled": False, "adapters": [], "nonebot_env": {}, "intercept_all": True}


@router.put("/config")
async def save_bridge_config(config: Dict[str, Any]) -> Dict[str, str]:
    """保存 NoneBot 桥接频道的配置。"""
    import json
    from pathlib import Path

    cfg_file = Path("channels/nonebot_bridge/channel_config.json")
    try:
        existing: Dict[str, Any] = {}
        if cfg_file.exists():
            existing = json.loads(cfg_file.read_text("utf-8"))

        existing.update(config)
        cfg_file.write_bytes(json.dumps(existing, indent=2, ensure_ascii=False).encode("utf-8"))
        return {"status": "ok"}
    except Exception as exc:
        raise HTTPException(500, str(exc))


def _check_adapter_installed(import_path: str) -> bool:
    """检查适配器包是否已安装。"""
    import importlib
    try:
        importlib.import_module(import_path)
        return True
    except ImportError:
        return False
