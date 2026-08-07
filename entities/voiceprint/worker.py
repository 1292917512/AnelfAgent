"""转写文本向量的后台回填（EmbeddingWorker backlog 挂接）。

voice_segments.transcript 的文本向量由记忆子系统的 EmbeddingWorker 统一消化：
写入路径（ingest）只需 wake worker，本模块的 backlog handler 每批取出
transcript_embedding IS NULL 的片段批量 embed 回填并同步 vec 索引。
"""

from __future__ import annotations

from typing import Any

from core.log import log

from .store import get_voiceprint_store


async def _segments_backfill(embedder: Any, batch_size: int) -> int:
    """EmbeddingWorker backlog 处理器：回填语音片段缺失的转写文本向量。"""
    store = get_voiceprint_store()
    rows = await store.list_missing_transcript_embeddings(batch_size)
    if not rows:
        return 0
    texts = [row["transcript"] for row in rows]
    vectors = await embedder.embed_text(texts)
    count = 0
    for row, vec in zip(rows, vectors, strict=False):
        if vec:
            await store.set_transcript_embedding(int(row["id"]), vec)
            count += 1
    return count


try:
    from agent.memory.embedding import register_embedding_backlog
    register_embedding_backlog("voiceprint_segments", _segments_backfill)
except Exception as _exc:
    log(f"音源库 embedding backlog 注册失败: {_exc}", "DEBUG", tag="音源库")
