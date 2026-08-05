"""媒体库实体的 HTTP 路由（自动挂载到 /api/entity/media）。

经 web/server.py 的 _mount_entity_routers 扫描发现。
- /config：媒体库富配置读写（provider 优先级链 / 默认参数 / 风格预设）
- /providers：各 provider 能力清单与配置状态（面板展示与排障）
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Body, HTTPException

from core.log import log

from . import config as media_config
from .providers import PROVIDER_NAMES, provider_status

# 允许通过 API 更新的顶层配置键
_EDITABLE_KEYS = (
    "provider_priority",
    "default_voice",
    "default_reference_audio",
    "default_reference_text",
    "defaults",
    "style_presets",
)


def _validate(payload: Dict[str, Any]) -> None:
    """校验配置载荷：provider 名合法、优先级链为字符串列表。"""
    priority = payload.get("provider_priority")
    if priority is not None:
        if not isinstance(priority, dict):
            raise HTTPException(status_code=400, detail="provider_priority 必须是对象")
        for cap, chain in priority.items():
            if not isinstance(chain, list) or not all(isinstance(p, str) for p in chain):
                raise HTTPException(status_code=400, detail=f"provider_priority.{cap} 必须是字符串数组")
            unknown = [p for p in chain if p not in PROVIDER_NAMES]
            if unknown:
                raise HTTPException(
                    status_code=400,
                    detail=f"provider_priority.{cap} 含未知 provider: {', '.join(unknown)}（可选: {', '.join(PROVIDER_NAMES)}）",
                )
    for key in ("defaults", "style_presets"):
        if key in payload and not isinstance(payload[key], dict):
            raise HTTPException(status_code=400, detail=f"{key} 必须是对象")


def build_router() -> APIRouter:
    router = APIRouter()

    @router.get("/config")
    async def get_config() -> Dict[str, Any]:
        """读取媒体库配置（含默认值合并后的生效值）。"""
        return media_config.load_config()

    @router.put("/config")
    async def put_config(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
        """合并更新媒体库配置（仅允许白名单顶层键）。"""
        _validate(payload)
        current = media_config.load_config()
        for key in _EDITABLE_KEYS:
            if key in payload:
                current[key] = payload[key]
        saved = media_config.save_config(current)
        log(f"媒体库配置已更新: {sorted(k for k in payload if k in _EDITABLE_KEYS)}", tag="媒体")
        return saved

    @router.get("/providers")
    async def list_providers() -> Dict[str, Any]:
        """各 provider 的能力清单与配置状态 + 当前优先级链。"""
        return {
            "providers": provider_status(),
            "provider_priority": media_config.load_config().get("provider_priority", {}),
        }

    return router
