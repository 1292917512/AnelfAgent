"""hooks_llm 管理路由（/api/hooks-llm）— LLM 钩子面的注册表观测与运行期启停。

钩子的开关与治理参数经统一配置面（/api/config/meta，hooks_llm/* 组）热调，
本路由只提供面板观测数据与运行期启停（注册表内存态），不另设写路径。
"""

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
    """运行期启停指定钩子（注册表内存态，热生效；重启恢复代码声明初始值）。"""
    try:
        return hooks_llm_service.set_enabled(name, body.enabled)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
