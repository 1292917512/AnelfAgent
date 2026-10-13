"""判断服务商与模型管理、通道状态和连接测试。"""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

from services.judgment import judgment_service

router = APIRouter(prefix="/judgment", tags=["judgment"])


class ModelSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model_id: str = ""


@router.get("/providers")
async def list_judgment_providers() -> Dict[str, Any]:
    try:
        return judgment_service.catalog()
    except (ValueError, OSError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.put("/providers/{provider_id}")
async def save_judgment_provider(provider_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return judgment_service.update("save_provider", provider_id, body)
    except (ValueError, OSError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.delete("/providers/{provider_id}")
async def delete_judgment_provider(provider_id: str) -> Dict[str, Any]:
    try:
        return judgment_service.update("delete_provider", provider_id)
    except (ValueError, OSError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.put("/default")
async def set_judgment_default(body: ModelSelection) -> Dict[str, Any]:
    try:
        return judgment_service.update("set_default", body.model_id)
    except (ValueError, OSError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.put("/models/{model_id}")
async def save_judgment_model(model_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return judgment_service.update("save_model", model_id, body)
    except (ValueError, OSError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.delete("/models/{model_id}")
async def delete_judgment_model(model_id: str) -> Dict[str, Any]:
    try:
        return judgment_service.update("delete_model", model_id)
    except (ValueError, OSError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/status")
async def get_judgment_status() -> Dict[str, Any]:
    """判断通道状态（启用态 / 当前通道 / 密钥已配与否 / 回退配置）。"""
    return judgment_service.status()


@router.post("/test")
async def test_judgment(body: ModelSelection | None = None) -> Dict[str, Any]:
    """用固定样例经当前通道跑一次判断（三题型各一），返回来源/答案/耗时。"""
    try:
        return await judgment_service.test(body.model_id if body else "")
    except (ValueError, OSError) as exc:
        raise HTTPException(400, str(exc)) from exc
