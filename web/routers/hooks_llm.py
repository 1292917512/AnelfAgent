"""hooks_llm 管理路由（/api/hooks-llm）— LLM 钩子面的注册表观测（只读）。

钩子的开关与治理参数经统一配置面（/api/config/meta，hooks_llm/* 组）热调，
本路由只提供面板观测数据，不另设写路径。
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter

from services.hooks_llm import hooks_llm_service

router = APIRouter(prefix="/hooks-llm", tags=["hooks-llm"])


@router.get("")
async def get_hooks_llm() -> Dict[str, Any]:
    """LLM 钩子面总览（启用状态 + 全部已注册钩子 + 治理配置）。"""
    return hooks_llm_service.get_overview()
