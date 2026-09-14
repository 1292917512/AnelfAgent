"""本地模型资产 — 语音链路等本地推理模型的注册、下载与校验。

资产登记（来源/版本/SHA-256 固定），落盘 ``workspace/models/``（AI
工作路径内，文件工具可直接查看）；下载为流式落盘 + 哈希校验 + 原子
替换，进度可查询。AI 经 list/download/delete_local_model 工具自主
安装维护，Web 经 services/model_assets 提供同一能力。

运行时依赖（onnxruntime）不在此安装——经 install_python_packages
工具或 Web 设置页装入 Python 环境。
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

from core.log import log
from core.path import workspace_root
from core.tool_errors import ErrorCause, error_from_exception, tool_error
from entities._sdk import deferred_tool

_LOG_TAG = "模型资产"

_PART_SUFFIX = ".part"


@dataclass(frozen=True)
class ModelAsset:
    """一项可下载的本地模型资产（版本与哈希固定，更新即换源重发版）。"""

    id: str
    filename: str
    name: str
    version: str
    url: str
    sha256: str
    license: str
    description: str
    pip_requires: str = ""
    """运行所需 pip 包（缺失时资产状态为 runtime_missing）。"""
    import_check: str = ""
    """运行时存在性探测的模块名（与 pip_requires 配套）。"""


MODEL_ASSETS: Sequence[ModelAsset] = (
    ModelAsset(
        id="silero_vad",
        filename="silero_vad.onnx",
        name="Silero VAD",
        version="v6.2.1",
        url="https://raw.githubusercontent.com/snakers4/silero-vad/v6.2.1/src/silero_vad/data/silero_vad.onnx",
        sha256="1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3",
        license="MIT",
        description="语音活动检测：逐帧判断是否有人在说话，噪音/喘息场景下显著稳于能量法",
        pip_requires="onnxruntime",
        import_check="onnxruntime",
    ),
    ModelAsset(
        id="smart_turn",
        filename="smart_turn_v3.onnx",
        name="SmartTurn 语义端点",
        version="v3.2-cpu",
        url="https://huggingface.co/pipecat-ai/smart-turn-v3/resolve/f766f81d3cfdf7737ac64aad813d91bbfd56bf93/smart-turn-v3.2-cpu.onnx?download=true",
        sha256="2bb026316b14a660486a75b1733cd3fbab8c2fd0314dc9af7be49f8cca967e4f",
        license="BSD-2-Clause",
        description="语义端点检测：判断用户语义上是否说完，避免停顿思考被误切（需配合 VAD）",
        pip_requires="onnxruntime",
        import_check="onnxruntime",
    ),
)


def models_dir() -> str:
    """模型落盘目录（AI 工作路径下的 models/，随 workspace 配置走）。"""
    return os.path.join(workspace_root(), "models")


def runtime_ready(asset: ModelAsset) -> bool:
    """资产的运行时依赖是否已安装（无依赖视为就绪）。"""
    if not asset.import_check:
        return True
    return importlib.util.find_spec(asset.import_check) is not None


class ModelAssetManager:
    """模型资产的下载/校验/删除管理（进程内单例，事件循环内使用）。"""

    def __init__(
        self,
        assets: Sequence[ModelAsset] = MODEL_ASSETS,
        directory: Optional[str] = None,
    ) -> None:
        self._assets = tuple(assets)
        self._directory = directory
        self._states: Dict[str, Dict[str, Any]] = {}
        self._tasks: Dict[str, asyncio.Task] = {}
        self._hash_cache: Dict[str, tuple] = {}

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def dir_path(self) -> str:
        return self._directory or models_dir()

    def asset(self, asset_id: str) -> ModelAsset:
        for asset in self._assets:
            if asset.id == asset_id:
                return asset
        raise KeyError(f"未知模型资产: {asset_id}")

    def path_of(self, asset: ModelAsset) -> str:
        return os.path.join(self.dir_path(), asset.filename)

    def verify(self, asset: ModelAsset) -> bool:
        """文件存在且 SHA-256 匹配（按 size+mtime 缓存，避免反复全量读）。"""
        path = self.path_of(asset)
        try:
            st = os.stat(path)
        except OSError:
            self._hash_cache.pop(path, None)
            return False
        key = (st.st_size, st.st_mtime_ns)
        cached = self._hash_cache.get(path)
        if cached is not None and cached[0] == key:
            return cached[1]
        digest = hashlib.sha256()
        try:
            with open(path, "rb") as fh:
                for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                    digest.update(chunk)
        except OSError:
            return False
        ok = digest.hexdigest().lower() == asset.sha256.lower()
        self._hash_cache[path] = (key, ok)
        return ok

    def resolve(self, asset_id: str) -> Optional[str]:
        """返回可放心加载的模型路径（未就绪返回 None）。"""
        try:
            asset = self.asset(asset_id)
        except KeyError:
            return None
        return self.path_of(asset) if self.verify(asset) else None

    def snapshot(self) -> List[Dict[str, Any]]:
        """全部资产的状态快照（Web 面板与 AI 工具共用）。"""
        out: List[Dict[str, Any]] = []
        for asset in self._assets:
            path = self.path_of(asset)
            state = dict(self._states.get(asset.id, {}))
            downloading = state.get("status") == "downloading"
            if not downloading:
                if state.get("status") not in ("error",):
                    state["status"] = "ready" if self.verify(asset) else "missing"
                state.pop("received", None)
                state.pop("total", None)
            try:
                size = os.path.getsize(path) if os.path.exists(path) else 0
            except OSError:
                size = 0
            out.append({
                "id": asset.id,
                "name": asset.name,
                "filename": asset.filename,
                "version": asset.version,
                "license": asset.license,
                "description": asset.description,
                "url": asset.url,
                "path": path,
                "size_bytes": size,
                "runtime_ready": runtime_ready(asset),
                "pip_requires": asset.pip_requires,
                **state,
            })
        return out

    # ------------------------------------------------------------------
    # 下载 / 删除
    # ------------------------------------------------------------------

    async def download(self, asset_id: str) -> Dict[str, Any]:
        """下载资产并等待完成（同一资产并发请求合并为同一次下载）。"""
        asset = self.asset(asset_id)
        self.start_download(asset_id)
        task = self._tasks[asset.id]
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            raise
        except Exception:
            pass  # 结果经状态呈现，不向调用方抛
        entry = next(e for e in self.snapshot() if e["id"] == asset.id)
        return entry

    def start_download(self, asset_id: str) -> Dict[str, Any]:
        """启动下载不等待（Web 面板轮询进度用）；已在途则原样返回。"""
        asset = self.asset(asset_id)
        task = self._tasks.get(asset.id)
        if task is None or task.done():
            self._states[asset.id] = {"status": "downloading", "received": 0, "total": 0}
            task = asyncio.create_task(
                self._download_asset(asset), name=f"model.download.{asset.id}")
            self._tasks[asset.id] = task
        return next(e for e in self.snapshot() if e["id"] == asset.id)

    async def _download_asset(self, asset: ModelAsset) -> None:
        path = self.path_of(asset)
        part = path + _PART_SUFFIX
        self._states[asset.id] = {"status": "downloading", "received": 0, "total": 0}
        os.makedirs(os.path.dirname(path), exist_ok=True)
        try:
            import aiohttp

            digest = hashlib.sha256()
            timeout = aiohttp.ClientTimeout(connect=15, sock_read=120)
            last_exc: Exception | None = None
            async with aiohttp.ClientSession(timeout=timeout) as session:
                for url in self._sources(asset):
                    try:
                        async with session.get(url) as resp:
                            resp.raise_for_status()
                            total = int(resp.headers.get("Content-Length") or 0)
                            received = 0
                            with open(part, "wb") as fh:
                                async for chunk in resp.content.iter_chunked(1024 * 256):
                                    fh.write(chunk)
                                    digest.update(chunk)
                                    received += len(chunk)
                                    self._states[asset.id] = {
                                        "status": "downloading",
                                        "received": received, "total": total,
                                    }
                        last_exc = None
                        break
                    except (aiohttp.ClientConnectionError, asyncio.TimeoutError) as exc:
                        last_exc = exc
                        log(f"模型源不可达，尝试下一候选: {url} - {exc}",
                            "DEBUG", tag=_LOG_TAG)
                if last_exc is not None:
                    raise last_exc
            if digest.hexdigest().lower() != asset.sha256.lower():
                raise IOError(
                    f"SHA-256 校验失败（预期 {asset.sha256[:12]}…，"
                    f"实际 {digest.hexdigest()[:12]}…）")
            os.replace(part, path)
            self._hash_cache.pop(path, None)
            self._states[asset.id] = {"status": "ready"}
            log(f"模型资产就绪: {asset.id} {asset.version} → {path}",
                "INFO", tag=_LOG_TAG)
        except Exception as exc:
            try:
                os.remove(part)
            except OSError:
                pass
            self._states[asset.id] = {"status": "error", "error": str(exc)}
            log(f"模型资产下载失败: {asset.id} - {exc}", "WARNING", tag=_LOG_TAG)
            raise

    @staticmethod
    def _sources(asset: ModelAsset) -> list[str]:
        """下载候选源：直连优先，直连受限网络自动回退镜像（model_asset_mirror）。"""
        from core.config import get_config

        mirror = str(get_config("model_asset_mirror", "auto") or "auto").strip()
        urls = [asset.url]
        if asset.url.startswith("https://huggingface.co/"):
            hf_mirror = "https://hf-mirror.com/"
            if mirror == "auto":
                urls.append(asset.url.replace("https://huggingface.co/", hf_mirror, 1))
            elif mirror.startswith("https://"):
                urls.append(asset.url.replace("https://huggingface.co/", mirror, 1))
        return urls

    def delete(self, asset_id: str) -> Dict[str, Any]:
        """删除资产文件（下载中拒绝）。"""
        asset = self.asset(asset_id)
        state = self._states.get(asset.id, {})
        if state.get("status") == "downloading":
            raise RuntimeError(f"{asset.id} 正在下载，先等待完成")
        path = self.path_of(asset)
        removed = False
        if os.path.exists(path):
            os.remove(path)
            self._hash_cache.pop(path, None)
            removed = True
        self._states.pop(asset.id, None)
        return {"id": asset.id, "removed": removed}


# 进程内单例（测试可替换）
_manager: Optional[ModelAssetManager] = None


def get_model_asset_manager() -> ModelAssetManager:
    global _manager
    if _manager is None:
        _manager = ModelAssetManager()
    return _manager


def reset_model_asset_manager() -> None:
    global _manager
    _manager = None


def _runtime_hint(asset_id: str) -> str:
    mgr = get_model_asset_manager()
    asset = mgr.asset(asset_id)
    if runtime_ready(asset):
        return ""
    return (f"注意: 运行时依赖 {asset.pip_requires} 未安装——安装后模型才能被加载"
            f"（可调 install_python_packages 安装，或经 Web 设置页）")


# ==================================================================
# AI 工具面（语音链路模型的自助安装维护）
# ==================================================================

@deferred_tool(group="voice", tags=["core"], concurrency_safe=True)
async def list_local_models() -> str:
    """查看本地模型资产清单与状态（是否已下载/校验通过/运行时依赖就绪/下载进度）。

    含语音链路的 VAD 与语义端点检测模型；下载目录在 workspace/models/ 下。
    """
    try:
        return json.dumps({
            "models": get_model_asset_manager().snapshot(),
            "dir": models_dir(),
        }, ensure_ascii=False)
    except Exception as e:
        return error_from_exception(e, action="查看本地模型资产")


@deferred_tool(group="voice", tags=["core"], timeout=900.0)
async def download_local_model(model_id: str) -> str:
    """下载一个本地模型资产并等待完成（流式下载 + SHA-256 校验）。

    适用于：语音端点检测降级提示模型缺失、或需要更新重下时。
    可用 model_id 经 list_local_models 查看。

    Args:
        model_id: 资产标识（如 silero_vad / smart_turn）
    """
    mgr = get_model_asset_manager()
    try:
        mgr.asset(model_id)
    except KeyError as e:
        return tool_error(
            str(e), cause=ErrorCause.PARAM, retryable=False,
            hint=f"可用: {' / '.join(a.id for a in mgr._assets)}")
    try:
        entry = await mgr.download(model_id)
        hint = _runtime_hint(model_id)
        return json.dumps(entry | ({"hint": hint} if hint else {}),
                          ensure_ascii=False)
    except Exception as e:
        return error_from_exception(e, action=f"下载模型资产 {model_id}")


@deferred_tool(group="voice", tags=["core"])
async def delete_local_model(model_id: str) -> str:
    """删除一个已下载的本地模型资产（释放磁盘；下载中会拒绝）。"""
    mgr = get_model_asset_manager()
    try:
        return json.dumps(mgr.delete(model_id), ensure_ascii=False)
    except KeyError as e:
        return tool_error(str(e), cause=ErrorCause.PARAM, retryable=False)
    except Exception as e:
        return error_from_exception(e, action=f"删除模型资产 {model_id}")
