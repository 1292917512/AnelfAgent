"""标签管理 API 路由。"""

from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services import TagService

router = APIRouter(prefix="/tags", tags=["tags"])

_tag_svc = TagService()


class TagCreate(BaseModel):
    name: str
    description: str


@router.post("/message")
async def create_message_tag(body: TagCreate) -> Dict[str, Any]:
    """创建自定义消息标签。"""
    try:
        return _tag_svc.create_tag(body.name.strip(), body.description.strip())
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.delete("/message/{name}")
async def delete_message_tag(name: str) -> Dict[str, Any]:
    """删除自定义消息标签（内置标签不可删除）。"""
    try:
        ok = _tag_svc.delete_tag(name)
        if not ok:
            raise HTTPException(404, f"标签 '{name}' 不存在")
        return {"status": "ok", "name": name}
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/tool")
async def list_tool_tags() -> List[str]:
    """返回所有工具注册的路由 tag（去重排序）。"""
    return _tag_svc.list_tool_tags()


@router.get("/unified")
async def list_unified_tags() -> List[Dict[str, Any]]:
    """返回统一标签列表（消息上下文标签 + 工具路由标签合并去重）。"""
    return _tag_svc.list_unified_tags()
