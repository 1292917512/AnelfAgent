"""实体管理 API 路由。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services.entity import EntityService

router = APIRouter(prefix="/entities", tags=["entities"])

_entity_svc = EntityService()


class ConfigUpdateRequest(BaseModel):
    key: str
    value: Any


class EnableRequest(BaseModel):
    enabled: bool


@router.get("/")
async def list_entities(
    entity_type: Optional[str] = None,
    group: Optional[str] = None,
) -> List[Dict[str, Any]]:
    return _entity_svc.list_entities(entity_type=entity_type, group=group)


@router.get("/catalog")
async def get_catalog() -> List[Dict[str, Any]]:
    return _entity_svc.get_catalog()


@router.get("/statistics")
async def get_statistics() -> Dict[str, Any]:
    return _entity_svc.get_statistics()


@router.get("/{name}")
async def get_entity_detail(name: str) -> Dict[str, Any]:
    detail = _entity_svc.get_entity_detail(name)
    if detail is None:
        raise HTTPException(404, f"实体 '{name}' 不存在")
    return detail


@router.get("/{name}/config")
async def get_entity_config(name: str) -> Dict[str, Any]:
    config = _entity_svc.get_entity_config(name)
    if config is None:
        raise HTTPException(404, f"实体 '{name}' 不存在")
    return config


@router.put("/{name}/config")
async def update_entity_config(
    name: str,
    body: ConfigUpdateRequest,
) -> Dict[str, Any]:
    ok = _entity_svc.update_entity_config(name, body.key, body.value)
    if not ok:
        raise HTTPException(400, "更新失败：实体或配置项不存在")
    return {"status": "ok", "key": body.key, "value": body.value}


@router.post("/{name}/enable")
async def set_entity_enabled(
    name: str,
    body: EnableRequest,
) -> Dict[str, Any]:
    ok = _entity_svc.set_entity_enabled(name, body.enabled)
    if not ok:
        raise HTTPException(404, f"实体 '{name}' 不存在")
    return {"name": name, "enabled": body.enabled}
