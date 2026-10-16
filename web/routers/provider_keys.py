"""组件凭据目录与卡片的原子保存接口。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services.provider_keys import ProviderKeyServiceFacade

router = APIRouter(prefix="/provider-keys", tags=["provider-keys"])

_facade = ProviderKeyServiceFacade()


class SetKeyRequest(BaseModel):
    values: dict[str, str]


@router.get("")
async def list_keys(domain: str = "") -> dict[str, Any]:
    """组件凭据清单（值脱敏；domain 过滤 sound/vision/…）。"""
    return _facade.list(domain)


@router.put("/{provider}")
async def set_key(provider: str, req: SetKeyRequest) -> dict[str, Any]:
    """原子保存凭据卡片的变更字段（缺省保持，空值清除）。"""
    try:
        return _facade.set(provider, req.values)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
