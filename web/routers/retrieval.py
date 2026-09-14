"""检索核心能力的 HTTP 路由（/api/retrieval）。

能力 × 提供者矩阵管理面：矩阵快照（凭据脱敏）、能力实现切换、
提供者启停、凭据配置、按能力连通性测试、抓取设置（代理/SSRF）。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services.retrieval import get_retrieval_service

router = APIRouter(prefix="/retrieval", tags=["retrieval"])


class SetActiveRequest(BaseModel):
    capability: str
    provider: str


class SetEnabledRequest(BaseModel):
    enabled: bool


class SetCredentialRequest(BaseModel):
    api_key: str = ""


class TestRequest(BaseModel):
    capability: str
    input: str = ""  # search→查询词 / reader→URL / repo→owner/repo


class SettingsRequest(BaseModel):
    proxy: Optional[str] = None
    ssrf_protection: Optional[bool] = None


@router.get("/matrix")
async def get_matrix() -> Dict[str, Any]:
    """能力 × 提供者矩阵快照（脱敏）。"""
    return get_retrieval_service().matrix()


@router.put("/active")
async def set_active(req: SetActiveRequest) -> Dict[str, Any]:
    """切换指定能力的提供者（auto 恢复自动选择）。"""
    status, out = get_retrieval_service().set_active(req.capability, req.provider)
    if status != 200:
        raise HTTPException(status_code=status, detail=out)
    return out


@router.put("/providers/{name}/enabled")
async def set_provider_enabled(name: str, req: SetEnabledRequest) -> Dict[str, Any]:
    """启用/停用提供者。"""
    status, out = get_retrieval_service().set_enabled(name, req.enabled)
    if status != 200:
        raise HTTPException(status_code=status, detail=out)
    return out


@router.put("/providers/{name}/credential")
async def set_provider_credential(name: str, req: SetCredentialRequest) -> Dict[str, Any]:
    """配置提供者 API Key（空串清除）。"""
    status, out = get_retrieval_service().set_credential(name, req.api_key)
    if status != 200:
        raise HTTPException(status_code=status, detail=out)
    return out


# 同步 def：FastAPI 自动投入线程池，避免阻塞事件循环
@router.post("/providers/{name}/test")
def test_provider(name: str, req: TestRequest) -> Dict[str, Any]:
    """用真实调用测试提供者指定能力的连通性。"""
    return get_retrieval_service().test_provider(name, req.capability, req.input)


@router.get("/settings")
async def get_settings() -> Dict[str, Any]:
    """抓取设置快照（代理/SSRF；凭据不回显）。"""
    return get_retrieval_service().settings()


@router.put("/settings")
async def save_settings(req: SettingsRequest) -> Dict[str, Any]:
    """保存抓取设置（None 字段不变更）。"""
    return get_retrieval_service().save_settings(
        proxy=req.proxy, ssrf_protection=req.ssrf_protection)
