"""Embedding 后台 worker 与批量回填链路测试。"""

from __future__ import annotations

import asyncio
import time

import pytest

from agent.memory.embedding_worker import (
    EmbeddingWorker,
    set_embedding_worker,
    wake_embedding_worker,
)
from agent.memory.memory_store import MemoryStore
from agent.memory.memory_types import MemoryEntry, MemoryType
from agent.memory.memory_utils import hash_text


class FakeEmbedder:
    """确定性 embedding  stub：embed 返回固定维度的单位向量。"""

    def __init__(self, dims: int = 4) -> None:
        self.dims = dims
        self.calls: list[list[str]] = []
        self._available = True

    @property
    def available(self) -> bool:
        return self._available

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [[1.0] + [0.0] * (self.dims - 1) for _ in texts]

    async def probe(self) -> bool:
        return self._available


@pytest.fixture
async def store(tmp_path):
    s = MemoryStore(str(tmp_path / "memory.sqlite3"))
    yield s
    await s.close()


def _entry(content: str) -> MemoryEntry:
    return MemoryEntry(
        memory_type=MemoryType.SEMANTIC,
        content=content,
        importance=0.5,
    )


async def _null_embedding_count(store: MemoryStore) -> int:
    db = await store._get_db()
    cursor = await db.execute(
        "SELECT COUNT(*) AS c FROM memories WHERE embedding_blob IS NULL"
    )
    row = await cursor.fetchone()
    return int(row["c"])


class TestBackfillEmbeddings:
    async def test_batch_backfill_memories(self, store: MemoryStore) -> None:
        for i in range(5):
            await store.add(_entry(f"记忆内容 {i}"))
        assert await _null_embedding_count(store) == 5

        embedder = FakeEmbedder()
        count = await store.backfill_embeddings(embedder, batch_size=32)

        assert count == 5
        assert await _null_embedding_count(store) == 0
        # 批量：5 条文本一次 API 调用完成
        assert len(embedder.calls) == 1
        assert len(embedder.calls[0]) == 5

    async def test_batch_size_respected(self, store: MemoryStore) -> None:
        for i in range(5):
            await store.add(_entry(f"记忆内容 {i}"))

        embedder = FakeEmbedder()
        count = await store.backfill_embeddings(embedder, batch_size=2)

        assert count == 2
        assert await _null_embedding_count(store) == 3

    async def test_empty_backfill_no_api_call(self, store: MemoryStore) -> None:
        embedder = FakeEmbedder()
        count = await store.backfill_embeddings(embedder, batch_size=32)
        assert count == 0
        assert embedder.calls == []

    async def test_embed_failure_keeps_backlog(self, store: MemoryStore) -> None:
        await store.add(_entry("待回填"))

        class FailEmbedder(FakeEmbedder):
            async def embed(self, texts: list[str]) -> list[list[float]]:
                return []

        count = await store.backfill_embeddings(FailEmbedder(), batch_size=32)
        assert count == 0
        assert await _null_embedding_count(store) == 1


class TestBackfillChunkEmbeddings:
    async def _insert_chunks(self, store: MemoryStore, n: int) -> None:
        now_ns = int(time.time() * 1e9)
        chunks = [
            {
                "id": f"doc.md:{i * 10}-{i * 10 + 9}",
                "path": "doc.md",
                "start_line": i * 10,
                "end_line": i * 10 + 9,
                "hash": hash_text(f"段落 {i}"),
                "text": f"段落 {i}",
                "embedding": None,
                "updated_ns": now_ns,
            }
            for i in range(n)
        ]
        await store.upsert_chunks(chunks)

    async def test_batch_backfill_chunks_and_cache(self, store: MemoryStore) -> None:
        await self._insert_chunks(store, 3)

        embedder = FakeEmbedder()
        count = await store.backfill_chunk_embeddings(embedder, batch_size=32)

        assert count == 3
        assert len(embedder.calls) == 1
        # chunk 向量已落库且写入缓存（下次同 hash 直接命中）
        db = await store._get_db()
        cursor = await db.execute("SELECT COUNT(*) AS c FROM chunks WHERE embedding IS NULL")
        assert int((await cursor.fetchone())["c"]) == 0
        cached = await store.get_cached_embedding(hash_text("段落 0"))
        assert cached is not None

    async def test_second_run_noop(self, store: MemoryStore) -> None:
        await self._insert_chunks(store, 2)
        embedder = FakeEmbedder()
        await store.backfill_chunk_embeddings(embedder, batch_size=32)
        assert await store.backfill_chunk_embeddings(embedder, batch_size=32) == 0
        assert len(embedder.calls) == 1


class TestPurgeArchivedMemories:
    async def _archive(self, store: MemoryStore, entry_id: int, archived_days_ago: float) -> None:
        db = await store._get_db()
        archived_ns = int((time.time() - archived_days_ago * 86400) * 1e9)
        await db.execute(
            "UPDATE memories_archive SET archived_at_ns=? WHERE id=?",
            (archived_ns, entry_id),
        )
        await db.commit()

    async def test_purge_only_expired(self, store: MemoryStore) -> None:
        old_id = await store.add(_entry("远古记忆"))
        new_id = await store.add(_entry("近期记忆"))
        for mid in (old_id, new_id):
            entry = await store.get(mid)
            assert entry is not None
            await store._archive_entry(entry, "test")
        await self._archive(store, old_id, archived_days_ago=120)
        await self._archive(store, new_id, archived_days_ago=10)

        deleted = await store.purge_archived_memories(older_than_days=90)

        assert deleted == 1
        archived = await store.list_archived()
        assert [a["id"] for a in archived] == [new_id]

    async def test_retention_zero_disabled(self, store: MemoryStore) -> None:
        mid = await store.add(_entry("归档记忆"))
        entry = await store.get(mid)
        assert entry is not None
        await store._archive_entry(entry, "test")
        await self._archive(store, mid, archived_days_ago=365)

        assert await store.purge_archived_memories(older_than_days=0) == 0
        assert len(await store.list_archived()) == 1


class TestEmbeddingWorker:
    async def test_drain_once_backfills_memories_and_chunks(self, store: MemoryStore) -> None:
        await store.add(_entry("worker 回填记忆"))
        now_ns = int(time.time() * 1e9)
        await store.upsert_chunks([{
            "id": "w.md:1-5", "path": "w.md", "start_line": 1, "end_line": 5,
            "hash": hash_text("worker chunk"), "text": "worker chunk",
            "embedding": None, "updated_ns": now_ns,
        }])

        embedder = FakeEmbedder()
        worker = EmbeddingWorker(store, embedder)  # type: ignore[arg-type]
        processed = await worker._drain_once()

        # memories 1 + chunks 1（对话回填在测试环境无 runtime，自动跳过）
        assert processed == 2
        assert await _null_embedding_count(store) == 0

    async def test_worker_loop_processes_on_wake(self, store: MemoryStore) -> None:
        embedder = FakeEmbedder()
        worker = EmbeddingWorker(store, embedder)  # type: ignore[arg-type]
        set_embedding_worker(worker)
        await worker.start()
        try:
            await store.add(_entry("唤醒后回填"))
            wake_embedding_worker()
            for _ in range(50):
                if await _null_embedding_count(store) == 0:
                    break
                await asyncio.sleep(0.05)
            assert await _null_embedding_count(store) == 0
        finally:
            await worker.close()
            set_embedding_worker(None)

    async def test_worker_backoff_when_embedder_unavailable(self, store: MemoryStore) -> None:
        embedder = FakeEmbedder()
        embedder._available = False
        worker = EmbeddingWorker(store, embedder)  # type: ignore[arg-type]
        await worker.start()
        await asyncio.sleep(0.1)
        assert worker._backoff_seconds >= 2.0
        await worker.close()
        assert worker._task is None

    async def test_close_without_start(self, store: MemoryStore) -> None:
        worker = EmbeddingWorker(store, FakeEmbedder())  # type: ignore[arg-type]
        await worker.close()
