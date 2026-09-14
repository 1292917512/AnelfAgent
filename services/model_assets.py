"""本地模型资产服务门面 — web 层与 agent/model_assets 之间的收口。"""

from __future__ import annotations

import asyncio
from typing import Any, Dict

from agent.model_assets import get_model_asset_manager, models_dir


class ModelAssetServiceFacade:
    """本地模型页签的数据与操作面（清单/下载/删除/运行时安装）。"""

    def list_models(self) -> Dict[str, Any]:
        """全部资产状态快照 + 落盘目录 + 运行时依赖状态。"""
        return {
            "models": get_model_asset_manager().snapshot(),
            "dir": models_dir(),
            "runtime": self.runtime_status(),
        }

    @staticmethod
    def runtime_status() -> Dict[str, Any]:
        """onnxruntime 运行时状态（模型推理的公共依赖）。"""
        try:
            import onnxruntime

            return {"installed": True, "version": onnxruntime.__version__}
        except Exception:
            return {"installed": False, "version": ""}

    def start_download(self, asset_id: str) -> Dict[str, Any]:
        """启动下载（进度经 list_models 轮询）。"""
        try:
            return get_model_asset_manager().start_download(asset_id)
        except KeyError as exc:
            raise ValueError(str(exc)) from exc

    def delete(self, asset_id: str) -> Dict[str, Any]:
        """删除已下载的模型文件。"""
        try:
            return get_model_asset_manager().delete(asset_id)
        except KeyError as exc:
            raise ValueError(str(exc)) from exc

    async def install_runtime(self, package: str = "onnxruntime") -> Dict[str, Any]:
        """安装运行时依赖包（uv/pip 按环境自动选择）。"""
        from entities.system.python_service import install_packages

        result = await asyncio.to_thread(install_packages, [package])
        return {
            "ok": result.ok, "package": package,
            "stdout": result.stdout[-2000:], "stderr": result.stderr[-2000:],
            "runtime": self.runtime_status(),
        }
