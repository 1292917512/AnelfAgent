from __future__ import annotations

import pytest

from agent.memory.memory_store import MemoryStore
from agent.memory.memory_types import MemoryEntry, MemoryType


@pytest.mark.asyncio
async def test_memory_write_and_projection_are_persisted_together(tmp_path) -> None:
    store = MemoryStore(str(tmp_path / "memory.sqlite3"))
    store.set_cognee_projection_enabled(True)
    try:
        memory_id = await store.add(MemoryEntry(
            memory_type=MemoryType.SEMANTIC,
            content="remember this",
            tags=["user:42"],
        ))

        entry = await store.get(memory_id)
        batch = await store.claim_cognee_sync_batch(10)

        assert entry is not None
        assert len(batch) == 1
        assert batch[0]["memory_id"] == memory_id
        assert batch[0]["operation"] == "upsert"
        assert batch[0]["payload"]["content"] == "remember this"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_projection_failure_can_be_retried(tmp_path) -> None:
    store = MemoryStore(str(tmp_path / "memory.sqlite3"))
    store.set_cognee_projection_enabled(True)
    try:
        memory_id = await store.add(MemoryEntry(
            memory_type=MemoryType.SEMANTIC,
            content="retry me",
        ))
        item = (await store.claim_cognee_sync_batch(1))[0]

        await store.fail_cognee_sync(
            item["queue_id"],
            "temporary",
            max_retries=1,
            retry_delay_seconds=1,
        )
        status = await store.get_cognee_sync_status()
        retried = await store.retry_failed_cognee_sync()

        assert memory_id > 0
        assert status["failed"] == 1
        assert retried == 1
        assert (await store.get_cognee_sync_status())["pending"] == 1
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_delete_queues_projection_cleanup(tmp_path) -> None:
    store = MemoryStore(str(tmp_path / "memory.sqlite3"))
    store.set_cognee_projection_enabled(True)
    try:
        memory_id = await store.add(MemoryEntry(
            memory_type=MemoryType.SEMANTIC,
            content="delete me",
        ))
        first = (await store.claim_cognee_sync_batch(1))[0]
        await store.complete_cognee_sync(
            first["queue_id"],
            memory_id,
            dataset_name="anelf_global",
            dataset_id="dataset-id",
            data_id="data-id",
        )

        assert await store.delete(memory_id)
        deletion = (await store.claim_cognee_sync_batch(1))[0]

        assert deletion["operation"] == "delete"
        mapping = await store.get_cognee_mapping(memory_id)
        assert mapping is not None
        assert mapping["data_id"] == "data-id"
    finally:
        await store.close()
