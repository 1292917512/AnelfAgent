"""系统工具 API 路由 -- 系统信息、Python 环境、Git 配置。

python_service / git_service 内部串行起大量 subprocess，均为同步阻塞实现，
async 处理器中一律经 asyncio.to_thread 移出事件循环。

服务重启/前端构建已收敛至 devops 实体（/api/entity/devops，entities/devops/）。
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/system", tags=["system"])

# ── 系统信息 ─────────────────────────────────────────────────────────

@router.get("/info")
async def get_system_info() -> Dict[str, Any]:
    from entities.system.info_service import get_dev_tools, get_python_info, get_system_info
    system, python, tools = await asyncio.gather(
        asyncio.to_thread(get_system_info),
        asyncio.to_thread(get_python_info),
        asyncio.to_thread(get_dev_tools),
    )
    return {
        "system": system,
        "python": python,
        "tools": tools,
    }

# ── Python 环境 ──────────────────────────────────────────────────────

@router.get("/python")
async def get_python_status() -> Dict[str, Any]:
    from entities.system.python_service import get_python_status
    return await asyncio.to_thread(get_python_status)


@router.get("/python/packages")
async def get_python_packages() -> List[Dict[str, str]]:
    from entities.system.python_service import get_installed_packages
    return await asyncio.to_thread(get_installed_packages)


class PipMirrorRequest(BaseModel):
    mirror_name: str


@router.post("/python/pip-mirror")
async def set_pip_mirror(req: PipMirrorRequest) -> Dict[str, Any]:
    from entities.system.python_service import set_pip_mirror
    result = await asyncio.to_thread(set_pip_mirror, req.mirror_name)
    return {"success": result.success, "output": result.output}


@router.get("/python/pip-mirror")
async def get_pip_mirror() -> Dict[str, Any]:
    from entities.system.python_service import get_pip_config
    return await asyncio.to_thread(get_pip_config)

# ── Git 配置 ─────────────────────────────────────────────────────────

@router.get("/git")
async def get_git_config() -> Dict[str, str]:
    from entities.system.git_service import get_user_config
    return await asyncio.to_thread(get_user_config)


class GitConfigUpdate(BaseModel):
    key: str
    value: str


@router.put("/git")
async def set_git_config(req: GitConfigUpdate) -> Dict[str, Any]:
    from entities.system.git_service import git_config_set
    ok, msg = await asyncio.to_thread(git_config_set, req.key, req.value)
    return {"ok": ok, "message": msg}


class GitProxyRequest(BaseModel):
    http_proxy: str = ""
    https_proxy: str = ""


@router.post("/git/proxy")
async def set_git_proxy(req: GitProxyRequest) -> Dict[str, Any]:
    from entities.system.git_service import set_proxy
    ok, msg = await asyncio.to_thread(set_proxy, req.http_proxy, req.https_proxy)
    return {"ok": ok, "message": msg}


@router.delete("/git/proxy")
async def unset_git_proxy() -> Dict[str, Any]:
    from entities.system.git_service import unset_proxy
    ok, msg = await asyncio.to_thread(unset_proxy)
    return {"ok": ok, "message": msg}


@router.post("/git/test")
async def test_github() -> Dict[str, Any]:
    from entities.system.git_service import test_github_connectivity
    return await asyncio.to_thread(test_github_connectivity)
