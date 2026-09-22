"""判断能力路由（/api/judgment）— 通道状态观测与连通性测试。

配置读写走统一配置面（/api/config/meta，judgment/core 组），本路由
不提供配置写路径。
"""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter

from services.judgment import judgment_service

router = APIRouter(prefix="/judgment", tags=["judgment"])


@router.get("/status")
async def get_judgment_status() -> Dict[str, Any]:
    """判断通道状态（启用态 / 当前通道 / 密钥已配与否 / 回退配置）。"""
    return judgment_service.status()


@router.post("/test")
async def test_judgment() -> Dict[str, Any]:
    """用固定样例经当前通道跑一次判断（三题型各一），返回来源/答案/耗时。"""
    return await judgment_service.test()
