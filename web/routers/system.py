"""系统工具 API 路由 -- 系统信息、Python 环境、Git 配置。

业务实现归 services.system.SystemService（内部经 asyncio.to_thread
把 entities.system 的同步阻塞 subprocess 移出事件循环）。

服务重启/前端构建已收敛至 devops 实体（/api/entity/devops，entities/devops/）。
"""

from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter
from pydantic import BaseModel

from services import SystemService

router = APIRouter(prefix="/system", tags=["system"])

_system_svc = SystemService()

# ── 系统信息 ─────────────────────────────────────────────────────────

@router.get("/info")
async def get_system_info() -> Dict[str, Any]:
    return await _system_svc.get_info()

# ── Python 环境 ──────────────────────────────────────────────────────

@router.get("/python")
async def get_python_status() -> Dict[str, Any]:
    return await _system_svc.get_python_status()


@router.get("/python/packages")
async def get_python_packages() -> List[Dict[str, str]]:
    return await _system_svc.get_installed_packages()


class PipMirrorRequest(BaseModel):
    mirror_name: str


@router.post("/python/pip-mirror")
async def set_pip_mirror(req: PipMirrorRequest) -> Dict[str, Any]:
    return await _system_svc.set_pip_mirror(req.mirror_name)


@router.get("/python/pip-mirror")
async def get_pip_mirror() -> Dict[str, Any]:
    return await _system_svc.get_pip_config()

# ── Git 配置 ─────────────────────────────────────────────────────────

@router.get("/git")
async def get_git_config() -> Dict[str, str]:
    return await _system_svc.get_git_config()


class GitConfigUpdate(BaseModel):
    key: str
    value: str


@router.put("/git")
async def set_git_config(req: GitConfigUpdate) -> Dict[str, Any]:
    return await _system_svc.set_git_config(req.key, req.value)


class GitProxyRequest(BaseModel):
    http_proxy: str = ""
    https_proxy: str = ""


@router.post("/git/proxy")
async def set_git_proxy(req: GitProxyRequest) -> Dict[str, Any]:
    return await _system_svc.set_git_proxy(req.http_proxy, req.https_proxy)


@router.delete("/git/proxy")
async def unset_git_proxy() -> Dict[str, Any]:
    return await _system_svc.unset_git_proxy()


@router.post("/git/test")
async def test_github() -> Dict[str, Any]:
    return await _system_svc.test_github_connectivity()
