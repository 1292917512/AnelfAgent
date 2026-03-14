"""人设管理 API 路由。"""

from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services import PersonaService

router = APIRouter(prefix="/personas", tags=["personas"])

_persona_svc = PersonaService()


@router.get("/")
async def list_personas() -> List[Dict[str, Any]]:
    return _persona_svc.list_personas()


@router.get("/active")
async def get_active() -> Dict[str, Any]:
    key = _persona_svc.get_active()
    return {"active": key}


@router.get("/{key}")
async def get_persona(key: str) -> Dict[str, Any]:
    try:
        return _persona_svc.get_persona(key)
    except Exception as e:
        raise HTTPException(404, str(e))


@router.put("/{key}")
async def save_persona(key: str, data: Dict[str, Any]) -> Dict[str, str]:
    _persona_svc.save_persona(key, data)
    return {"status": "ok"}


class CreatePersonaRequest(BaseModel):
    key: str


@router.post("/")
async def create_persona(req: CreatePersonaRequest) -> Dict[str, str]:
    try:
        _persona_svc.create(req.key)
        return {"status": "ok", "key": req.key}
    except ValueError as e:
        raise HTTPException(409, str(e))


@router.delete("/{key}")
async def delete_persona(key: str) -> Dict[str, Any]:
    ok = _persona_svc.delete(key)
    if not ok:
        raise HTTPException(400, "无法删除活跃人设")
    return {"status": "ok"}


@router.put("/{key}/activate")
async def activate_persona(key: str) -> Dict[str, Any]:
    ok = _persona_svc.activate(key)
    return {"ok": ok}
