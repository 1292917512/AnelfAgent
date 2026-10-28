"""会话上下文的认证读写入口；写入本身不发送消息或发起模型调用。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException

from .context import HttpContextSnapshot
from .schemas import ContextRequest, SessionTarget
from .transport import portal_dependencies

if TYPE_CHECKING:
    from .adapter import HttpApiChannel


def _response(scope: str, snapshot: HttpContextSnapshot, enabled: bool) -> dict[str, Any]:
    """返回连接当前保存的唯一快照，不包含认证凭据。"""
    return {"status": "ok", "scope": scope, "enabled": enabled,
            "dynamic_context": snapshot.dynamic_context, "skills": list(snapshot.skills)}


def build_context_router(adapter: HttpApiChannel) -> APIRouter:
    """读写使用消息入口的用户、群组和子会话规则，沿用统一门户认证。"""
    router = APIRouter(prefix="/api/sessions/context", tags=["context"], dependencies=portal_dependencies())

    @router.put("")
    async def update(body: ContextRequest) -> dict[str, Any]:
        try:
            snapshot = adapter.contexts.update(body.scope, dynamic_context=body.dynamic_context, skills=body.skills)
        except OverflowError as exc:
            raise HTTPException(503, str(exc)) from exc
        return _response(body.scope, snapshot, adapter.config.context_inject)

    @router.get("")
    async def read(target: SessionTarget = Depends()) -> dict[str, Any]:
        return _response(target.scope, adapter.contexts.get(target.scope), adapter.config.context_inject)

    @router.delete("")
    async def clear(target: SessionTarget = Depends()) -> dict[str, Any]:
        adapter.contexts.clear(target.scope)
        return _response(target.scope, adapter.contexts.get(target.scope), adapter.config.context_inject)

    return router
