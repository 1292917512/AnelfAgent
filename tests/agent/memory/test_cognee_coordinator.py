from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent.memory.cognee.config import CogneeConfig
from agent.memory.cognee.coordinator import CogneeCoordinator
from agent.memory.memory_store import MemoryStore
from agent.memory.memory_types import MemoryEntry, MemoryType


class _FakeCogneeClient:
    def __init__(self) -> None:
        self.items: list[dict] = []
        self.deleted: list[tuple[str, str]] = []

    async def initialize(self):
        return SimpleNamespace(ready=True, reason="")

    async def make_data_item(self, data: str, *, label: str, external_metadata: dict):
        return {"data": data, "label": label, "external_metadata": external_metadata}

    async def add(self, data, **_kwargs):
        self.items.extend(data)

    async def cognify(self, **_kwargs):
        return None

    async def improve(self, **_kwargs):
        return None

    async def list_datasets(self):
        return [SimpleNamespace(id="dataset-id", name="anelf_global")]

    async def list_data(self, _dataset_id):
        return [
            SimpleNamespace(
                id=f"data-{item['external_metadata']['anelf_memory_id']}",
                external_metadata=item["external_metadata"],
            )
            for item in self.items
        ]

    async def delete_data(self, dataset_id, data_id, **_kwargs):
        self.deleted.append((str(dataset_id), str(data_id)))


@pytest.mark.asyncio
async def test_coordinator_projects_and_deletes_memory(tmp_path) -> None:
    store = MemoryStore(str(tmp_path / "memory.sqlite3"))
    store.set_cognee_projection_enabled(True)
    client = _FakeCogneeClient()
    coordinator = CogneeCoordinator(
        store,
        client,
        CogneeConfig(enabled=True, sync_enabled=True),
    )
    try:
        memory_id = await store.add(MemoryEntry(
            memory_type=MemoryType.SEMANTIC,
            content="projected",
        ))
        await coordinator._process_batch(await store.claim_cognee_sync_batch(10))

        mapping = await store.get_cognee_mapping(memory_id)
        assert mapping is not None
        assert mapping["dataset_id"] == "dataset-id"
        assert mapping["data_id"] == f"data-{memory_id}"

        await store.delete(memory_id)
        await coordinator._process_batch(await store.claim_cognee_sync_batch(10))

        assert client.deleted[-1] == ("dataset-id", f"data-{memory_id}")
        assert await store.get_cognee_mapping(memory_id) is None
    finally:
        await store.close()
