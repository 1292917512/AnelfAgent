"""频道/适配器管理 API 路由。"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException

from services import AdapterService

router = APIRouter(prefix="/adapters", tags=["adapters"])

_adapter_svc = AdapterService()


@router.get("/")
async def list_adapters() -> Dict[str, Any]:
    adapters = _adapter_svc.list_adapters()
    if adapters is None:
        return {"ready": False, "adapters": []}
    return {"ready": True, "adapters": adapters}


@router.put("/{key}/toggle")
async def toggle_adapter(key: str) -> Dict[str, str]:
    loop = asyncio.get_running_loop()
    try:
        _adapter_svc.toggle_adapter(key, loop)
        return {"status": "ok"}
    except Exception as e:
        raise HTTPException(500, str(e))


@router.get("/configs")
async def get_configs() -> Dict[str, Dict[str, Any]]:
    return _adapter_svc.get_adapter_configs()


@router.put("/configs")
async def save_configs(values: Dict[str, Any]) -> Dict[str, int]:
    changed = _adapter_svc.save_adapter_configs(values)
    return {"changed": changed}
