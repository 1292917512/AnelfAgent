"""图片感知索引 worker：入站图片的后台沉淀（下载 → 哈希 → VLM 描述 → embedding 入库）。

参照 EmbeddingWorker 的 wake 模式：MediaPipeline 只负责投递（fire-and-forget），
worker 串行消化队列，避免阻塞消息管线；VLM 描述可配置关闭（省 token），
关闭时仍保留 phash / content_hash 索引（图搜图、去重仍可用）。
"""

from __future__ import annotations

import asyncio
import hashlib
import os
from typing import Optional, Tuple

from core.config import get_config_bool, get_config_float
from core.log import log

from .phash import compute_phash
from .store import StickerStore, get_sticker_store

_DESCRIBE_PROMPT = (
    "请用一到两句话客观描述这张图片的内容主体、场景和显著文字（如有），"
    "用于后续语义检索。只输出描述本身，不要输出其他内容。"
)

_worker: Optional["ImageIndexWorker"] = None


def set_image_index_worker(worker: Optional["ImageIndexWorker"]) -> None:
    global _worker
    _worker = worker


def get_image_index_worker() -> Optional["ImageIndexWorker"]:
    return _worker


def submit_image(path_or_url: str, source: str = "") -> None:
    """写入路径调用：投递一张图片到索引队列（无 worker 时 no-op）。"""
    if _worker and path_or_url:
        _worker.submit(path_or_url, source)


def _md5_file(path: str) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


class ImageIndexWorker:
    """后台串行索引任务：队列驱动，逐张处理（VLM 调用天然限流）。"""

    def __init__(self, store: Optional[StickerStore] = None) -> None:
        self.store = store or get_sticker_store()
        self._queue: asyncio.Queue[Tuple[str, str]] = asyncio.Queue(maxsize=200)
        self._task: Optional[asyncio.Task[None]] = None
        self._closing = False
        self._seen: set[str] = set()  # 进程内去重（path/url 级）

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name="sticker.image_index")

    async def close(self) -> None:
        self._closing = True
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    def submit(self, path_or_url: str, source: str = "") -> None:
        if self._closing or path_or_url in self._seen:
            return
        self._seen.add(path_or_url)
        if len(self._seen) > 5000:
            # 防内存膨胀：保留最近一半（set 无序，简单重建）
            self._seen = set(list(self._seen)[-2500:])
        try:
            self._queue.put_nowait((path_or_url, source))
        except asyncio.QueueFull:
            log("图片索引队列已满，丢弃新投递", "DEBUG", tag="贴纸")

    async def _loop(self) -> None:
        while not self._closing:
            try:
                path_or_url, source = await asyncio.wait_for(
                    self._queue.get(), timeout=30.0)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                raise
            try:
                await self._index_one(path_or_url, source)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log(f"图片索引失败 {path_or_url[:80]}: {exc}", "DEBUG", tag="贴纸")

    async def _localize(self, path_or_url: str) -> str:
        """URL 下载到 uploads；本地路径原样返回。失败返回空串。"""
        if path_or_url.startswith(("http://", "https://")):
            from agent.channel.media import download_to_uploads
            from agent.channel.schemas import SegmentType
            return await download_to_uploads(path_or_url, SegmentType.IMAGE)
        return path_or_url if os.path.exists(path_or_url) else ""

    async def _describe(self, local_path: str) -> str:
        """VLM 生成描述（可配置关闭；遍历全部视觉客户端直到成功）。"""
        if not get_config_bool("image_index_describe_enabled", True):
            return ""
        try:
            from entities._sdk import (
                get_llm_manager, get_model_type_enum, load_image_from_path,
            )
            mgr = get_llm_manager()
            ModelType = get_model_type_enum()
            vision_clients = mgr.get_all_by_type(ModelType.VISION)
            if not vision_clients:
                return ""
            img = load_image_from_path(local_path)
            timeout = get_config_float("image_index_describe_timeout", 60.0)
            for vc in vision_clients:
                try:
                    return await asyncio.wait_for(
                        vc.describe_images([img], prompt=_DESCRIBE_PROMPT),
                        timeout=timeout,
                    )
                except Exception as exc:
                    log(f"索引描述模型 {vc.config.name} 失败: {exc}", "DEBUG", tag="贴纸")
                    continue
        except Exception as exc:
            log(f"图片描述不可用: {exc}", "DEBUG", tag="贴纸")
        return ""

    async def _embed(self, text: str) -> Optional[list]:
        if not text:
            return None
        try:
            from agent.memory.embedder import Embedder
            embedder = Embedder()
            return await embedder.embed_one(text)
        except Exception:
            return None

    async def _index_one(self, path_or_url: str, source: str) -> None:
        local_path = await self._localize(path_or_url)
        if not local_path:
            return

        content_hash = await asyncio.to_thread(_md5_file, local_path)
        # 内容级去重：同一图片换 URL/路径重发不重复建索引
        existing = await self.store.get_image_by_hash(content_hash)
        if existing:
            return

        phash = await asyncio.to_thread(compute_phash, local_path)
        description = await self._describe(local_path)
        embedding = await self._embed(description)

        await self.store.upsert_image(
            path=local_path,
            description=description,
            content_hash=content_hash,
            phash=phash,
            source=source,
            embedding=embedding,
        )
        log(
            f"图片已索引: {os.path.basename(local_path)} "
            f"(phash={phash[:8] or '-'}, desc={'有' if description else '无'})",
            "DEBUG", tag="贴纸",
        )
