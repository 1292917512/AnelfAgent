"""组件凭据 API 路由（/api/provider-keys）— 域页签凭据面板的数据面。"""

from __future__ import annotations

from typing import Dict

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services.provider_keys import ProviderKeyServiceFacade

router = APIRouter(prefix="/provider-keys", tags=["provider-keys"])

_facade = ProviderKeyServiceFacade()


class SetKeyRequest(BaseModel):
    field: str = "api_key"
    value: str


@router.get("")
async def list_keys(domain: str = "") -> Dict:
    """组件凭据清单（值脱敏；domain 过滤 sound/vision/…）。"""
    return _facade.list(domain)


@router.put("/{provider}")
async def set_key(provider: str, req: SetKeyRequest) -> Dict:
    """写入一个凭据字段（空值清除）。"""
    try:
        return _facade.set(provider, req.field, req.value)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
