"""人脸入库 worker：入站图片的后台识别（下载 → 检测 → 识别建档 → 事件落库）。

参照 EmbeddingWorker / ImageIndexWorker 的 wake 模式：调用方只负责投递
（fire-and-forget），worker 串行消化队列，避免阻塞消息管线与视觉缓冲；
引擎调用是外部 GPU 服务的网络往返（百毫秒级），绝不同步挂在入站路径上。

三个投递入口（同一队列）：
- media_pipeline：频道入站图片（face_auto_ingest，带会话 scope 供打标）
- vision.buffer：视觉源变化帧（face_watch_enabled，默认关）
- AI 工具 / Web 面板：显式识别入库
"""

from __future__ import annotations

import asyncio
import os
from typing import Optional, Tuple

from core.config import get_config_bool
from core.log import log

from .ingest import ingest_image

_LOG_TAG = "人脸"


class FaceIngestWorker:
    """后台串行识别任务：队列驱动，逐张处理（引擎调用天然限流）。"""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[Tuple[str, str, str]] = asyncio.Queue(maxsize=200)
        self._task: Optional[asyncio.Task[None]] = None
        self._closing = False
        self._seen: set[str] = set()  # 进程内去重（path/url 级）

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name="face.ingest")

    async def close(self) -> None:
        self._closing = True
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass  # 取消属正常关闭流程（正常控制流，非异常）
            self._task = None

    def submit(self, path_or_url: str, source: str = "", scope: str = "") -> None:
        if self._closing or not path_or_url or path_or_url in self._seen:
            return
        self._seen.add(path_or_url)
        if len(self._seen) > 5000:
            # 防内存膨胀：保留最近一半（set 无序，简单重建）
            self._seen = set(list(self._seen)[-2500:])
        try:
            self._queue.put_nowait((path_or_url, source, scope))
        except asyncio.QueueFull:
            log("人脸入库队列已满，丢弃新投递", "DEBUG", tag=_LOG_TAG)

    async def _loop(self) -> None:
        while not self._closing:
            try:
                path_or_url, source, scope = await asyncio.wait_for(
                    self._queue.get(), timeout=30.0)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                raise
            try:
                await self._ingest_one(path_or_url, source, scope)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log(f"人脸入库失败 {path_or_url[:80]}: {exc}", "DEBUG", tag=_LOG_TAG)

    async def _localize(self, path_or_url: str) -> str:
        """URL 下载到 uploads；本地路径原样返回。失败返回空串。"""
        if path_or_url.startswith(("http://", "https://")):
            from entities._sdk import download_media_to_uploads
            return await download_media_to_uploads(path_or_url, "image")
        return path_or_url if os.path.exists(path_or_url) else ""

    async def _ingest_one(self, path_or_url: str, source: str, scope: str) -> None:
        local_path = await self._localize(path_or_url)
        if not local_path:
            return
        result = await ingest_image(local_path, source or "worker", scope=scope)
        if result.skipped:
            return
        matched = [h.person_name or h.person_key for h in result.hits if h.matched]
        log(
            f"图片人脸识别: {os.path.basename(local_path)} → "
            f"{len(result.hits)} 脸" + (f"（命中: {', '.join(matched)}）" if matched else ""),
            "DEBUG", tag=_LOG_TAG)


# ------------------------------------------------------------------
# 单例与投递入口（bootstrap 创建注册；未创建时投递为 no-op）
# ------------------------------------------------------------------

_worker: Optional[FaceIngestWorker] = None


def set_face_worker(worker: Optional[FaceIngestWorker]) -> None:
    """组合根注册/注销 worker 单例（bootstrap 专用）。"""
    global _worker
    _worker = worker


def get_face_worker() -> Optional[FaceIngestWorker]:
    return _worker


def submit_face_image(path_or_url: str, source: str = "", scope: str = "") -> bool:
    """投递一张图片到人脸入库队列（worker 未创建/自动识别关闭时 no-op）。

    Returns:
        是否成功投递（调用方据此做日志/降级，投递本身 fire-and-forget）。
    """
    if _worker is None or not path_or_url:
        return False
    if source.startswith("vision:") and not get_config_bool("face_watch_enabled", False):
        return False
    if source == "inbound" and not get_config_bool("face_auto_ingest", True):
        return False
    _worker.submit(path_or_url, source, scope)
    return True
