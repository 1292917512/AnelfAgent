"""Embedding 后台 worker：注册式 backlog 消化。

worker 不认识任何具体存储：各存储以回填处理器（BacklogHandler）挂接，
worker 按批次轮流消化并负责故障退避。memories / chunks 由 worker 内部
自注册（同属记忆子系统），对话消息等外部存储经 register_embedding_backlog
注入（worker 未就绪时挂起，创建后自动挂载）。

写入路径只落库（embedding 留 NULL）并 wake worker；限速 / 重试 / 超时
由 Embedder 引擎内部闭环，worker 不做额外的流量控制。
"""

from __future__ import annotations

import asyncio
from typing import Awaitable, Callable, Dict, Optional

from core.config import get_config_float, get_config_int, register_configs_safe
from core.latebind import LateBinding
from core.log import log

from ..memory_store import MemoryStore
from .engine import Embedder

BacklogHandler = Callable[[Embedder, int], Awaitable[int]]
"""回填处理器：接收 embedder 与批次大小，返回本轮补全的向量数。"""

_WORKER_CONFIGS = {
    "memory/embedding": {
        "embedding_worker_batch_size": {
            "description": "后台回填的单批文本数（单次 API 调用）",
            "default": 32,
            "advanced": True,
            "unit": "条",
        },
        "embedding_worker_interval_seconds": {
            "description": "后台 worker 空闲轮询间隔",
            "default": 30.0,
            "advanced": True,
            "unit": "秒",
        },
        "conv_embed_backfill_days": {
            "description": "对话消息回填的时间窗，远古消息不回填（0 = 不限）",
            "default": 30,
            "advanced": True,
            "unit": "天",
        },
    },
}

register_configs_safe(_WORKER_CONFIGS)

#: worker 端口（worker 在 bootstrap init_memory 节点创建后，经
#: agent.runtime.wiring 统一施绑；绑定后 attach_pending_backlogs 消化挂起注册）
embedding_worker_port: LateBinding["EmbeddingWorker"] = LateBinding("memory.embedding_worker")

# worker 施绑前注册的外部 backlog 挂起表（施绑时一次性挂载并清空）
_pending_backlogs: Dict[str, BacklogHandler] = {}


def get_embedding_worker() -> Optional["EmbeddingWorker"]:
    """取当前 worker（端口未施绑时返回 None，调用方按无 worker 降级）。"""
    return embedding_worker_port.get() if embedding_worker_port.bound else None


def attach_pending_backlogs(worker: "EmbeddingWorker") -> None:
    """把挂起的 backlog 注册挂载到 worker（组合根施绑端口后调用一次）。"""
    if _pending_backlogs:
        for name, handler in _pending_backlogs.items():
            worker.register_backlog(name, handler)
        _pending_backlogs.clear()


def wake_embedding_worker() -> None:
    """写入路径调用：通知 worker 有新 backlog（无 worker 时 no-op）。"""
    worker = get_embedding_worker()
    if worker:
        worker.wake()


def register_embedding_backlog(name: str, handler: BacklogHandler) -> None:
    """外部存储注册 backlog 回填处理器；worker 未施绑时挂起，施绑后自动挂载。"""
    worker = get_embedding_worker()
    if worker:
        worker.register_backlog(name, handler)
    else:
        _pending_backlogs[name] = handler


class EmbeddingWorker:
    """后台批量回填 embedding 的常驻任务。

    每轮遍历已注册的 backlog 各处理一批（批量 embed，单次 API 往返）；
    有积压时持续消化，空闲则睡眠等待 wake 或轮询超时；单个 backlog 失败
    不影响其他 backlog。embedder 不可用时按指数退避，避免 embedding
    服务故障时空转刷库。
    """

    def __init__(self, store: MemoryStore, embedder: Embedder) -> None:
        self.embedder = embedder
        self._task: Optional[asyncio.Task[None]] = None
        self._wake = asyncio.Event()
        self._closing = False
        self._backoff_seconds = 0.0
        self._backlogs: Dict[str, BacklogHandler] = {
            "memories": lambda e, bs: store.backfill_embeddings(e, bs),
            "chunks": lambda e, bs: store.backfill_chunk_embeddings(e, bs),
        }

    def register_backlog(self, name: str, handler: BacklogHandler) -> None:
        """挂接一个 backlog 来源（同名覆盖）。"""
        self._backlogs[name] = handler

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
                pass  # 取消属正常关闭流程（正常控制流，非异常）
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
                self._backoff_seconds = 0.0
                if processed:
                    continue
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
                pass  # 超时属正常等待结束（正常控制流，非异常）

    async def _drain_once(self) -> int:
        """每个已注册 backlog 各处理一批，返回本轮补全的向量总数。"""
        total = 0
        for name, handler in list(self._backlogs.items()):
            try:
                total += await handler(self.embedder, self._batch_size)
            except Exception as exc:
                log(f"Embedding 回填[{name}]失败: {exc}", "DEBUG", tag="思维")
        return total

    async def _sleep_backoff(self) -> None:
        """embedder 故障时的指数退避（2s 起，上限 300s），期间仍响应关闭。"""
        self._backoff_seconds = min(300.0, max(2.0, self._backoff_seconds * 2))
        self._wake.clear()
        try:
            await asyncio.wait_for(self._wake.wait(), timeout=self._backoff_seconds)
        except asyncio.TimeoutError:
            pass  # 超时属正常等待结束（正常控制流，非异常）
