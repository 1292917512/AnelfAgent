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


@router.get("/health")
async def library_health() -> Dict[str, Any]:
    """技能库健康报告（计数/零参与/高匹配零消费/触发词碰撞/向量构建状态）。"""
    return _skill_svc.library_health()


@router.post("/vectors/rebuild")
async def rebuild_vectors() -> Dict[str, Any]:
    """手动触发全量向量重建（模型切换后的标准操作）。

    幂等：进行中直接返回当前进度。返回触发后的构建状态快照。
    """
    try:
        return await _skill_svc.rebuild_vectors()
    except RuntimeError as e:
        raise HTTPException(503, str(e)) from e


@router.post("/{name}/embed")
async def embed_skill(name: str) -> Dict[str, Any]:
    """单个技能向量生成/重新生成（行内操作）。

    幂等：向量已就绪直接返回；重建进行中让位（rebuild_all 会覆盖该技能）。
    """
    try:
        return await _skill_svc.embed_skill(name)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
    except RuntimeError as e:
        raise HTTPException(503, str(e)) from e


@router.get("/{name}")
async def get_skill(name: str) -> Dict[str, Any]:
    try:
        return _skill_svc.get_skill(name)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e


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
    rationale: Optional[str] = None


@router.put("/{name}")
async def update_skill(name: str, req: UpdateSkillRequest) -> Dict[str, Any]:
    try:
        return _skill_svc.update_skill(name, req.model_dump(exclude_none=True))
    except ValueError as e:
        raise HTTPException(404, str(e)) from e


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
        raise HTTPException(400, str(e)) from e


class SetPinnedRequest(BaseModel):
    pinned: bool


@router.post("/{name}/pinned")
async def set_skill_pinned(name: str, req: SetPinnedRequest) -> Dict[str, Any]:
    try:
        return _skill_svc.set_pinned(name, req.pinned)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
