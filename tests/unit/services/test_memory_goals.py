"""MemoryService goals CRUD 回归测试。

回归点：list_goals 曾读取 MemoryEntry 不存在的 created_at 属性，
导致 GET /memory/goals 一律 500（应使用 entry.timestamp）。
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from agent.memory.memory_store import MemoryStore
from agent.memory.memory_types import MemoryEntry, MemoryType
from services.memory import MemoryService


@pytest.fixture
def svc(store: MemoryStore, monkeypatch) -> MemoryService:
    rt = SimpleNamespace(mind=SimpleNamespace(memory_store=store))
    monkeypatch.setattr("services.memory.require_runtime", lambda: rt)
    return MemoryService()


def _make_goal(goal_id: str = "g1", title: str = "测试目标") -> dict:
    return {
        "goal_id": goal_id, "title": title, "description": "",
        "status": "active", "recurring": False, "steps": [],
        "created_at": "2026-08-05 10:00:00", "updated_at": "2026-08-05 10:00:00",
    }


class TestListGoals:
    async def test_list_returns_goals_with_created_ts(self, svc: MemoryService, store: MemoryStore) -> None:
        await store.add(MemoryEntry(
            memory_type=MemoryType.SEMANTIC,
            content=json.dumps(_make_goal(), ensure_ascii=False),
            source="goal", importance=0.8,
        ))
        goals = await svc.list_goals()
        assert len(goals) == 1
        assert goals[0]["goal_id"] == "g1"
        # created_ts 来自 entry.timestamp（Unix 秒），不再访问不存在的 created_at
        assert goals[0]["created_ts"] > 0

    async def test_status_filter(self, svc: MemoryService, store: MemoryStore) -> None:
        await store.add(MemoryEntry(
            memory_type=MemoryType.SEMANTIC,
            content=json.dumps(_make_goal(), ensure_ascii=False),
            source="goal", importance=0.8,
        ))
        assert await svc.list_goals(status="completed") == []
        assert len(await svc.list_goals(status="active")) == 1


class TestUpdateGoal:
    async def test_update_status(self, svc: MemoryService, store: MemoryStore) -> None:
        await store.add(MemoryEntry(
            memory_type=MemoryType.SEMANTIC,
            content=json.dumps(_make_goal(), ensure_ascii=False),
            source="goal", importance=0.8,
        ))
        updated = await svc.update_goal("g1", status="completed")
        assert updated is not None
        assert updated["status"] == "completed"

        goals = await svc.list_goals()
        assert len(goals) == 1
        assert goals[0]["status"] == "completed"

    async def test_update_missing_returns_none(self, svc: MemoryService) -> None:
        assert await svc.update_goal("nonexistent", status="completed") is None
