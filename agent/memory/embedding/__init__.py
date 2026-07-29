"""embedding 子系统门面：引擎（调用管理） + worker（后台消化）。

- engine.Embedder: embedding 调用的唯一入口（限速/超时/重试/降级内闭环）
- worker.EmbeddingWorker: 注册式 backlog 后台消化
"""

from .engine import Embedder, get_embedder, invalidate_embedders
from .worker import (
    BacklogHandler,
    EmbeddingWorker,
    get_embedding_worker,
    register_embedding_backlog,
    set_embedding_worker,
    wake_embedding_worker,
)

__all__ = [
    "Embedder",
    "get_embedder",
    "invalidate_embedders",
    "EmbeddingWorker",
    "BacklogHandler",
    "get_embedding_worker",
    "set_embedding_worker",
    "wake_embedding_worker",
    "register_embedding_backlog",
]
