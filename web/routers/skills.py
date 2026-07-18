"""技能管理 API 路由。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services.skill import SkillService

router = APIRouter(prefix="/skills", tags=["skills"])

_skill_svc = SkillService()


@router.get("/")
async def list_skills(include_archived: bool = False) -> List[Dict[str, Any]]:
    return _skill_svc.list_skills(include_archived=include_archived)


@router.get("/{name}")
async def get_skill(name: str) -> Dict[str, Any]:
    try:
        return _skill_svc.get_skill(name)
    except ValueError as e:
        raise HTTPException(404, str(e))


class CreateSkillRequest(BaseModel):
    name: str
    description: str = ""
    content: str = ""
    trigger_patterns: Optional[List[str]] = None


@router.post("/")
async def create_skill(req: CreateSkillRequest) -> Dict[str, Any]:
    if not req.name.strip():
        raise HTTPException(400, "技能名不能为空")
    return _skill_svc.create_skill(
        name=req.name,
        description=req.description,
        content=req.content,
        trigger_patterns=req.trigger_patterns,
    )


class UpdateSkillRequest(BaseModel):
    content: Optional[str] = None
    description: Optional[str] = None
    add_trigger_patterns: Optional[List[str]] = None


@router.put("/{name}")
async def update_skill(name: str, req: UpdateSkillRequest) -> Dict[str, Any]:
    try:
        return _skill_svc.update_skill(name, req.model_dump(exclude_none=True))
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.delete("/{name}")
async def delete_skill(name: str) -> Dict[str, str]:
    if not _skill_svc.delete_skill(name):
        raise HTTPException(404, f"技能 '{name}' 不存在")
    return {"status": "ok"}


class SetStateRequest(BaseModel):
    state: str


@router.post("/{name}/state")
async def set_skill_state(name: str, req: SetStateRequest) -> Dict[str, Any]:
    try:
        return _skill_svc.set_state(name, req.state)
    except ValueError as e:
        raise HTTPException(400, str(e))


class SetPinnedRequest(BaseModel):
    pinned: bool


@router.post("/{name}/pinned")
async def set_skill_pinned(name: str, req: SetPinnedRequest) -> Dict[str, Any]:
    try:
        return _skill_svc.set_pinned(name, req.pinned)
    except ValueError as e:
        raise HTTPException(404, str(e))
