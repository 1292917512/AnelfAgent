"""记忆详情 HTTP 契约：完整原文可读，读取不改写权威记忆或访问计数。"""

from types import SimpleNamespace

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from agent.memory.memory_store import MemoryStore
from agent.memory.memory_types import MemoryEntry, MemoryType
from web.routers.memory import router


async def test_ltm_detail_serializes_entry_and_preserves_read_only_contract(store: MemoryStore, monkeypatch) -> None:
    monkeypatch.setattr("services.memory.require_runtime", lambda: SimpleNamespace(memory_store=store))
    mid = await store.add(MemoryEntry(
        content="已经更新的完整事实，保留限定条件。" * 100,
        memory_type=MemoryType.SEMANTIC, tags=["person:甲", "state:current"],
        metadata={"origins": [{"kind": "note", "scope": "project:验收"}]}, embedding=[0.1, 0.2],
    ))
    before = await store.get(mid)
    app = FastAPI()
    app.include_router(router)
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as http:
        result = await http.get(f"/memory/ltm/{mid}")
        assert result.status_code == 200
        assert result.json() == before.model_dump(mode="json")
        assert await store.get(mid) == before
        assert await store.archive_memory(mid)
        missing = await http.get(f"/memory/ltm/{mid}")
        assert missing.status_code == 404
