"""目标的三个单条删除入口共用所见版本、去向审计与事件。"""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from agent.memory.memory_store import MemoryStore
from agent.memory.memory_types import GOAL_SOURCE, MemoryEntry, MemoryType
from agent.planning import tools, tracker
from agent.planning.goals import GoalConflict
from core.event_bus import EVENT_PLAN_DELETED, event_bus
from web.routers.memory import router


@pytest.mark.parametrize("source", ["goal", "ltm", "tool"])
async def test_goal_deletion_uses_observed_version(source: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = MemoryStore(str(tmp_path / "memory.sqlite3"))
    monkeypatch.setattr("services.memory.require_runtime", lambda: SimpleNamespace(memory_store=store))
    monkeypatch.setattr(tracker, "get_planning_store", lambda: store)
    app = FastAPI()
    app.include_router(router)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            goal = (await client.post("/memory/goals", json={"title": "原目标"})).json()
            goal_id, version = goal["goal_id"], goal["version"]
            url = f"/memory/goals/{goal_id}"
            delete_url = f"/memory/ltm/{goal['memory_id']}" if source == "ltm" else url
            latest = (await client.put(url, json={"title": "新进度", "expected_version": version})).json()
            emit = AsyncMock()
            monkeypatch.setattr(event_bus, "emit", emit)
            if source == "tool":
                for observed in (None, version):
                    assert json.loads(await tools.delete_goal(goal_id, observed))["cause"] == "state"
            else:
                assert (await client.delete(delete_url)).status_code == 428
                assert (await client.delete(delete_url, params={"expected_version": version})).status_code == 409
            assert (await client.get(url)).json() == latest
            assert await store.list_audit(goal_id=goal_id) == []
            emit.assert_not_awaited()

            if source == "tool":
                assert json.loads(await tools.delete_goal(goal_id, latest["version"]))["success"]
            else:
                response = await client.delete(delete_url, params={"expected_version": latest["version"]})
                assert response.status_code == 200 and response.json()["ok"]
            receipt = (await client.get(url)).json()
            assert receipt["deleted"] and receipt["title"] == "新进度"
            assert len(await store.list_audit(goal_id=goal_id)) == 1
            assert emit.await_count == 1 and emit.await_args.args[0] == EVENT_PLAN_DELETED
    finally:
        await store.close()


async def test_ltm_deletes_ordinary_memory_and_handles_legacy_goal_content(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = MemoryStore(str(tmp_path / "memory.sqlite3"))
    monkeypatch.setattr("services.memory.require_runtime", lambda: SimpleNamespace(memory_store=store, embedder=None))
    monkeypatch.setattr("agent.memory.cognee.runtime.get_cognee_client", lambda: None)
    app = FastAPI()
    app.include_router(router)
    try:
        plain = await store.add(MemoryEntry(memory_type=MemoryType.SEMANTIC, content="笔记"))
        entry = await store.get(plain)
        assert entry is not None
        assert await store.update(entry.model_copy(update={"content": "auditrevisionneedle"}), expected_version=entry.version)
        legacy = await store.add(MemoryEntry(memory_type=MemoryType.SEMANTIC, content="旧目标文本",
                                             source=GOAL_SOURCE, metadata={"goal_id": "legacy"}))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            search = await client.get("/memory/ltm/search", params={"query": "auditrevisionneedle"})
            hit = next(row for row in search.json() if row["id"] == f"mem:{plain}")
            assert hit["source"] == "memory" and hit["version"] == entry.version + 1
            assert (await client.delete(f"/memory/ltm/{plain}")).status_code == 200
            assert (await client.delete(f"/memory/ltm/{legacy}")).status_code == 428
            items = (await client.get("/memory/ltm/paginated")).json()["items"]
            response = await client.delete(f"/memory/ltm/{legacy}", params={"expected_version": items[0]["version"]})
            assert response.status_code == 200
            receipt = (await client.get("/memory/goals/legacy")).json()
            assert receipt["goal_id"] == "legacy" and receipt["deleted"]
    finally:
        await store.close()


async def test_goal_changed_after_lookup_survives_database_delete(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = MemoryStore(str(tmp_path / "memory.sqlite3"))
    emit = AsyncMock()
    monkeypatch.setattr(event_bus, "emit", emit)
    try:
        mem_id = await store.add(MemoryEntry(memory_type=MemoryType.SEMANTIC, source=GOAL_SOURCE,
                                              content='{"goal_id":"race","title":"初稿"}'))
        snapshot = await store.get(mem_id)
        assert snapshot is not None
        latest = snapshot.model_copy(update={"content": '{"goal_id":"race","title":"新进度"}'})
        assert await store.update(latest, expected_version=snapshot.version)
        with pytest.raises(GoalConflict):
            await tracker.remove_goal(snapshot, store=store, expected_version=snapshot.version)
        remaining = await store.get(mem_id)
        assert remaining is not None and remaining.content == latest.content
        assert await store.list_audit(goal_id="race") == []
        emit.assert_not_awaited()
    finally:
        await store.close()
