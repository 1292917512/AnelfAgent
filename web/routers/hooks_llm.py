"""LLM 钩子声明观测及统一配置开关入口。"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services.hooks_llm import hooks_llm_service

router = APIRouter(prefix="/hooks-llm", tags=["hooks-llm"])


class HooksLlmToggleBody(BaseModel):
    """启停请求体：钩子名 + 目标状态。"""

    enabled: bool


@router.get("")
async def get_hooks_llm() -> Dict[str, Any]:
    """LLM 钩子面总览（启用状态 + 全部已注册钩子 + 治理配置）。"""
    return hooks_llm_service.get_overview()


@router.post("/{name}/enabled")
async def set_hook_enabled(name: str, body: HooksLlmToggleBody) -> Dict[str, Any]:
    """保存钩子开关，后续调度按最新配置执行。"""
    try:
        return hooks_llm_service.set_enabled(name, body.enabled)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
