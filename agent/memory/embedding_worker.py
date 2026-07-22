"""Embedding 后台 worker：异步消化 memories / chunks / 对话消息的向量回填。

参照 CogneeCoordinator 的 outbox + wake 模式：写入路径只落库（embedding 留 NULL）
并 wake worker，由后台任务批量调用 embedding API 补全，避免阻塞请求路径。
"""

from __future__ import annotations

import asyncio
from typing import Optional

from core.config import get_config_float, get_config_int, register_configs_safe
from core.log import log

from .embedder import Embedder
from .memory_store import MemoryStore

_WORKER_CONFIGS = {
    "记忆": {
        "embedding_worker_batch_size": {
            "description": "Embedding 后台回填的单批文本数（单次 API 调用）",
            "default": 32,
        },
        "embedding_worker_interval_seconds": {
            "description": "Embedding 后台 worker 空闲轮询间隔（秒）",
            "default": 30.0,
        },
        "conv_embed_backfill_days": {
            "description": "对话消息 embedding 回填的时间窗（天），远古消息不回填（0 = 不限）",
            "default": 30,
        },
    },
}

register_configs_safe(_WORKER_CONFIGS)

_worker: Optional["EmbeddingWorker"] = None


def set_embedding_worker(worker: Optional["EmbeddingWorker"]) -> None:
    global _worker
    _worker = worker


def get_embedding_worker() -> Optional["EmbeddingWorker"]:
    return _worker


def wake_embedding_worker() -> None:
    """写入路径调用：通知 worker 有新 backlog（无 worker 时 no-op）。"""
    if _worker:
        _worker.wake()


class EmbeddingWorker:
    """后台批量回填 embedding 的常驻任务。

    每轮依次处理 memories / chunks / conversation_messages 三类 backlog
    各一批（批量 embed，单次 API 往返）；有空闲则睡眠等待 wake 或轮询超时。
    embedder 不可用时按指数退避，避免 embedding 服务故障时空转刷库。
    """

    def __init__(self, store: MemoryStore, embedder: Embedder) -> None:
        self.store = store
        self.embedder = embedder
        self._task: Optional[asyncio.Task[None]] = None
        self._wake = asyncio.Event()
        self._closing = False
        self._backoff_seconds = 0.0

    @property
    def _batch_size(self) -> int:
        return max(1, get_config_int("embedding_worker_batch_size", 32))

    @property
    def _interval_seconds(self) -> float:
        return max(5.0, get_config_float("embedding_worker_interval_seconds", 30.0))

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._worker(), name="memory.embedding")
        self.wake()

    async def close(self) -> None:
        self._closing = True
        self._wake.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    def wake(self) -> None:
        self._wake.set()

    async def _worker(self) -> None:
        while not self._closing:
            try:
                if not self.embedder.available:
                    # 故障后通过 probe 探测恢复（available 失败后不会自行重置）
                    try:
                        recovered = await self.embedder.probe()
                    except Exception:
                        recovered = False
                    if not recovered:
                        await self._sleep_backoff()
                        continue
                processed = await self._drain_once()
                if processed:
                    self._backoff_seconds = 0.0
                    continue
                self._backoff_seconds = 0.0
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log(f"Embedding worker 循环异常: {exc}", "WARNING", tag="思维")
                await self._sleep_backoff()
                continue

            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self._interval_seconds)
            except asyncio.TimeoutError:
                pass

    async def _drain_once(self) -> int:
        """每类 backlog 各处理一批，返回本轮换补的向量总数。"""
        batch = self._batch_size
        total = await self.store.backfill_embeddings(self.embedder, batch)
        total += await self.store.backfill_chunk_embeddings(self.embedder, batch)
        total += await self._backfill_conversations(batch)
        return total

    async def _backfill_conversations(self, batch_size: int) -> int:
        """回填对话消息 embedding（best-effort：storage 未就绪时跳过）。"""
        try:
            from services._runtime import require_runtime
            sqlite = require_runtime().data_center.sqlite
        except Exception:
            return 0
        # 对话回填批次复用现有配置（WebUI 可调），缺省跟随通用批次
        conv_batch = get_config_int("conv_recall_backfill_batch", batch_size)
        max_age_days = get_config_int("conv_embed_backfill_days", 30)
        try:
            return await sqlite.backfill_conversation_embeddings(
                self.embedder,
                batch_size=conv_batch,
                max_age_days=max_age_days,
            )
        except Exception as exc:
            log(f"对话 embedding 回填失败: {exc}", "DEBUG", tag="思维")
            return 0

    async def _sleep_backoff(self) -> None:
        """embedder 故障时的指数退避（2s 起，上限 300s），期间仍响应关闭。"""
        self._backoff_seconds = min(300.0, max(2.0, self._backoff_seconds * 2))
        self._wake.clear()
        try:
            await asyncio.wait_for(self._wake.wait(), timeout=self._backoff_seconds)
        except asyncio.TimeoutError:
            pass
