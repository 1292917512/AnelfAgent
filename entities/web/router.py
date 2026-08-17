"""web 实体的 HTTP 路由（自动挂载到 /api/entity/web）。

能力 × 提供者矩阵管理面：矩阵快照（凭据脱敏，仅暴露来源标记）、
能力实现切换、提供者启停、凭据配置、按能力连通性测试。
代理等通用设置仍走 /api/config/web-tools。
"""

from __future__ import annotations

import time
from typing import Dict, List, Tuple

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from core.log import log
from core.sanitizer import sanitize_text


class ProviderOut(BaseModel):
    """提供者出站模型（脱敏：不含凭据本体，仅来源标记）。"""

    name: str
    display_name: str
    description: str
    enabled: bool
    configured: bool
    requires_credential: bool
    credential_source: str = ""
    capabilities: List[str]


class MatrixOut(BaseModel):
    """能力 × 提供者矩阵快照。"""

    capabilities: List[str]
    selection: Dict[str, str]          # capability -> 配置的固定选择（auto=自动）
    active: Dict[str, str]             # capability -> 当前生效提供者（无可用为 ""）
    providers: List[ProviderOut]


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


class TestResult(BaseModel):
    ok: bool
    latency_ms: int = 0
    summary: str = ""
    excerpt: str = ""
    error: str = ""


def build_router() -> APIRouter:
    router = APIRouter()

    def _matrix() -> MatrixOut:
        from entities.web import providers
        from entities.web.providers.base import CAPABILITY_PROTOCOLS
        from entities.web.web_config import get_active
        active: Dict[str, str] = {}
        for cap in CAPABILITY_PROTOCOLS:
            try:
                active[cap] = providers.resolve(cap).name
            except ValueError:
                active[cap] = ""
        return MatrixOut(
            capabilities=list(CAPABILITY_PROTOCOLS),
            selection={cap: get_active(cap) for cap in CAPABILITY_PROTOCOLS},
            active=active,
            providers=[
                ProviderOut(
                    name=p.name,
                    display_name=p.display_name,
                    description=p.description,
                    enabled=p.enabled(),
                    configured=p.configured(),
                    requires_credential=p.requires_credential,
                    credential_source=p.credential()[1],
                    capabilities=providers.provider_capabilities(p),
                )
                for p in providers.list_providers()
            ],
        )

    @router.get("/matrix", response_model=MatrixOut)
    async def get_matrix() -> MatrixOut:
        """能力 × 提供者矩阵快照（脱敏）。"""
        return _matrix()

    @router.put("/active", response_model=MatrixOut)
    async def set_active(req: SetActiveRequest) -> MatrixOut:
        """切换指定能力的提供者（auto 恢复自动选择）。"""
        from entities.web import providers
        from entities.web.providers.base import CAPABILITY_PROTOCOLS
        from entities.web.web_config import set_active as save_active
        cap = req.capability.strip()
        if cap not in CAPABILITY_PROTOCOLS:
            raise HTTPException(status_code=404, detail=f"未知能力: {cap}")
        name = req.provider.strip()
        if name != "auto":
            try:
                providers.resolve(cap, name)  # 不支持/已禁用/未配置带原因抛出
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e)) from e
        save_active(cap, name or "auto")
        return _matrix()

    @router.put("/providers/{name}/enabled", response_model=MatrixOut)
    async def set_provider_enabled(name: str, req: SetEnabledRequest) -> MatrixOut:
        """启用/禁用提供者。"""
        from entities.web import providers
        from entities.web.web_config import set_enabled
        try:
            providers.get_provider(name)
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        set_enabled(name, req.enabled)
        return _matrix()

    @router.put("/providers/{name}/credential", response_model=MatrixOut)
    async def set_provider_credential(name: str, req: SetCredentialRequest) -> MatrixOut:
        """配置提供者 API Key（空串清除）。"""
        from entities.web import providers
        try:
            provider = providers.get_provider(name)
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        if not provider.requires_credential:
            raise HTTPException(status_code=400, detail=f"提供者 {name} 无需凭据")
        provider.set_api_key(req.api_key)
        return _matrix()

    # 同步 def：FastAPI 自动投入线程池，避免阻塞事件循环
    @router.post("/providers/{name}/test", response_model=TestResult)
    def test_provider(name: str, req: TestRequest) -> TestResult:
        """用真实调用测试提供者指定能力的连通性。"""
        from entities.web import providers
        from entities.web.providers.base import CAPABILITY_PROTOCOLS
        cap = req.capability.strip()
        if cap not in CAPABILITY_PROTOCOLS:
            raise HTTPException(status_code=404, detail=f"未知能力: {cap}")
        try:
            provider = providers.resolve(cap, name)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

        started = time.monotonic()
        try:
            summary, excerpt = _run_capability_test(provider, cap, req.input.strip())
        except Exception as e:
            log(f"提供者能力测试失败 [{name}/{cap}]: {e}", "WARNING", tag="Web")
            return TestResult(ok=False, error=sanitize_text(str(e))[:500])
        return TestResult(
            ok=True,
            latency_ms=int((time.monotonic() - started) * 1000),
            summary=summary,
            excerpt=excerpt,
        )

    return router


def _run_capability_test(provider: object, capability: str, user_input: str) -> Tuple[str, str]:
    """按能力执行一次真实调用，返回 (summary, excerpt)。"""
    from entities.web.providers.base import (
        CAP_READER,
        CAP_REPO,
        CAP_SEARCH,
        ReaderCap,
        RepoCap,
        SearchCap,
    )
    if capability == CAP_SEARCH and isinstance(provider, SearchCap):
        output = provider.search(user_input or "今日新闻", 3)
        refs = output.get("references", [])
        excerpt = "\n".join(f"{r.get('title', '')} — {r.get('url', '')}" for r in refs[:3])
        return f"{output.get('sources', 0)} 条结果", excerpt
    if capability == CAP_READER and isinstance(provider, ReaderCap):
        output = provider.read(user_input or "https://example.com", timeout=20)
        content = str(output.get("content", ""))
        return str(output.get("title") or output.get("url", "")), content[:300]
    if capability == CAP_REPO and isinstance(provider, RepoCap):
        content = provider.get_repo_structure(user_input or "vitejs/vite")
        return user_input or "vitejs/vite", content[:300]
    raise ValueError(f"提供者不支持该能力: {capability}")
