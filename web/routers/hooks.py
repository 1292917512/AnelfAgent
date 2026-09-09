"""hooks 管理路由（/api/hooks）— 用户 hook 配置的读取、校验写入与样例。"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, HTTPException

from services.hooks import hook_service

router = APIRouter(prefix="/hooks", tags=["hooks"])


@router.get("")
async def get_hooks() -> Dict[str, Any]:
    """当前 hooks 配置与运行时生效状态。"""
    return hook_service.get_hooks()


@router.put("")
async def save_hooks(body: Dict[str, Any]) -> Dict[str, Any]:
    """全量替换 hooks 配置（校验通过才落盘并立即热生效）。"""
    try:
        return hook_service.save_hooks(body)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/example")
async def get_example() -> Dict[str, Any]:
    """样例配置（前端编辑器初始内容）。"""
    return hook_service.get_example()
