"""embedding 子系统门面：引擎（调用管理） + worker（后台消化）。

- engine.Embedder: embedding 调用的唯一入口（限速/超时/重试/降级内闭环）
- worker.EmbeddingWorker: 注册式 backlog 后台消化
- usage: 调用量日级账本（calls/texts/chars，经 /status/usage 暴露）
"""

from .engine import Embedder, get_embedder, invalidate_embedders
from .usage import embedding_usage_summary, flush_embedding_usage, record_embedding_call
from .worker import (
    BacklogHandler,
    EmbeddingWorker,
    get_embedding_worker,
    register_embedding_backlog,
    wake_embedding_worker,
)

__all__ = [
    "Embedder",
    "get_embedder",
    "invalidate_embedders",
    "EmbeddingWorker",
    "BacklogHandler",
    "get_embedding_worker",
    "wake_embedding_worker",
    "register_embedding_backlog",
    "record_embedding_call",
    "flush_embedding_usage",
    "embedding_usage_summary",
]
