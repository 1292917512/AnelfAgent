"""本地模型资产 API 路由（/api/local-models）— 模型下载/删除与运行时安装。"""

from __future__ import annotations

from typing import Dict

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services.model_assets import ModelAssetServiceFacade

router = APIRouter(prefix="/local-models", tags=["local-models"])

_assets = ModelAssetServiceFacade()


class RuntimeInstallRequest(BaseModel):
    package: str = "onnxruntime"


@router.get("")
async def list_models() -> Dict:
    """全部本地模型资产状态（含下载进度）+ 运行时依赖状态。"""
    return _assets.list_models()


@router.post("/{asset_id}/download")
async def download(asset_id: str) -> Dict:
    """启动资产下载（进度经 GET 轮询）。"""
    try:
        return _assets.start_download(asset_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.delete("/{asset_id}")
async def remove(asset_id: str) -> Dict:
    """删除已下载的模型文件（下载中拒绝）。"""
    try:
        return _assets.delete(asset_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/runtime")
async def runtime_status() -> Dict:
    """运行时依赖（onnxruntime）状态。"""
    return _assets.runtime_status()


@router.post("/runtime/install")
async def install_runtime(req: RuntimeInstallRequest) -> Dict:
    """安装运行时依赖包（同步等待完成；装完即生效）。"""
    if not req.package.strip() or any(c in req.package for c in ";&|`$"):
        raise HTTPException(400, "非法包名")
    return await _assets.install_runtime(req.package.strip())
