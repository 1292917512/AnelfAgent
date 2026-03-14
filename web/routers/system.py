"""系统工具 API 路由 -- 系统信息、Python 环境、Git 配置。"""

from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/system", tags=["system"])

# ── 系统信息 ─────────────────────────────────────────────────────────

@router.get("/info")
async def get_system_info() -> Dict[str, Any]:
    from entities.system.info_service import get_system_info, get_python_info, get_dev_tools
    return {
        "system": get_system_info(),
        "python": get_python_info(),
        "tools": get_dev_tools(),
    }

# ── Python 环境 ──────────────────────────────────────────────────────

@router.get("/python")
async def get_python_status() -> Dict[str, Any]:
    from entities.system.python_service import get_python_status
    return get_python_status()


@router.get("/python/packages")
async def get_python_packages() -> List[Dict[str, str]]:
    from entities.system.python_service import get_installed_packages
    return get_installed_packages()


class PipMirrorRequest(BaseModel):
    mirror_name: str


@router.post("/python/pip-mirror")
async def set_pip_mirror(req: PipMirrorRequest) -> Dict[str, Any]:
    from entities.system.python_service import set_pip_mirror
    result = set_pip_mirror(req.mirror_name)
    return {"success": result.success, "output": result.output}


@router.get("/python/pip-mirror")
async def get_pip_mirror() -> Dict[str, Any]:
    from entities.system.python_service import get_pip_config
    return get_pip_config()

# ── Git 配置 ─────────────────────────────────────────────────────────

@router.get("/git")
async def get_git_config() -> Dict[str, str]:
    from entities.system.git_service import get_user_config
    return get_user_config()


class GitConfigUpdate(BaseModel):
    key: str
    value: str


@router.put("/git")
async def set_git_config(req: GitConfigUpdate) -> Dict[str, Any]:
    from entities.system.git_service import git_config_set
    ok, msg = git_config_set(req.key, req.value)
    return {"ok": ok, "message": msg}


class GitProxyRequest(BaseModel):
    http_proxy: str = ""
    https_proxy: str = ""


@router.post("/git/proxy")
async def set_git_proxy(req: GitProxyRequest) -> Dict[str, Any]:
    from entities.system.git_service import set_proxy
    ok, msg = set_proxy(req.http_proxy, req.https_proxy)
    return {"ok": ok, "message": msg}


@router.delete("/git/proxy")
async def unset_git_proxy() -> Dict[str, Any]:
    from entities.system.git_service import unset_proxy
    ok, msg = unset_proxy()
    return {"ok": ok, "message": msg}


@router.post("/git/test")
async def test_github() -> Dict[str, Any]:
    from entities.system.git_service import test_github_connectivity
    return test_github_connectivity()
