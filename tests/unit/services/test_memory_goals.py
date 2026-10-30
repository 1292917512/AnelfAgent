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
    rt = SimpleNamespace(memory_store=store)
    monkeypatch.setattr("services.memory.require_runtime", lambda: rt)
    return MemoryService()


def _make_goal(goal_id: str = "g1", title: str = "测试目标") -> dict:
    return {
        "goal_id": goal_id, "title": title, "description": "",
        "status": "active", "recurring": False, "steps": [],
        "created_at": "2026-08-05 10:00:00", "updated_at": "2026-08-05 10:00:00",
    }


class TestListGoals:
    async def test_archived_goal_is_explicit_and_read_only(self, svc: MemoryService, store: MemoryStore) -> None:
        mid = await store.add(MemoryEntry(
            memory_type=MemoryType.SEMANTIC, content=json.dumps(_make_goal()),
        ))
        assert await store.archive_memory(mid, reason="legacy")
        db = await store._get_db()
        async with store._tx(db):
            await db.execute("UPDATE memories_archive SET source='goal' WHERE id=?", (mid,))
        assert await svc.list_goals() == []
        archived = await svc.list_goals(include_archived=True)
        assert len(archived) == 1 and archived[0]["archived"]
        assert archived[0]["archive_reason"] == "legacy"
        assert (await svc.get_goal("g1"))["archived"]
        assert await svc.update_goal("g1", status="completed") is None
        assert not await svc.delete_goal("g1")
        assert (await store.resolve(mid))["status"] == "archived"

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
    @pytest.mark.parametrize("status", ["completed", "cancelled"])
    async def test_update_status(self, svc: MemoryService, store: MemoryStore, status: str) -> None:
        await store.add(MemoryEntry(
            memory_type=MemoryType.SEMANTIC,
            content=json.dumps(_make_goal(), ensure_ascii=False),
            source="goal", importance=0.8,
        ))
        updated = await svc.update_goal("g1", status=status)
        assert updated is not None
        assert updated["status"] == status

        # 终态即清（与 AI 工具面同步语义）：条目不保留
        assert await svc.list_goals() == []
        audit = await store.list_audit(memory_id=updated["memory_id"])
        assert audit[0]["actor"] == "web"
        assert audit[0]["data"]["goal_id"] == "g1"
        assert audit[0]["data"]["status"] == status

    async def test_update_steps_all_done_autocloses(self, svc: MemoryService, store: MemoryStore) -> None:
        """全部步骤 done 的 steps 替换触发自动收口（与 AI 工具面同步语义）。"""
        await store.add(MemoryEntry(
            memory_type=MemoryType.SEMANTIC,
            content=json.dumps(_make_goal(), ensure_ascii=False),
            source="goal", importance=0.8,
        ))
        updated = await svc.update_goal("g1", steps=[
            {"step": "a", "status": "completed"},
            {"step": "b", "status": "completed"},
        ])
        assert updated is not None
        assert await svc.list_goals() == []
        assert updated["status"] == "completed"
        audit = await store.list_audit(memory_id=updated["memory_id"])
        assert audit[0]["data"]["status"] == "completed"

    async def test_update_steps_partial_keeps(self, svc: MemoryService, store: MemoryStore) -> None:
        """步骤未全部 done 时正常写回。"""
        await store.add(MemoryEntry(
            memory_type=MemoryType.SEMANTIC,
            content=json.dumps(_make_goal(), ensure_ascii=False),
            source="goal", importance=0.8,
        ))
        updated = await svc.update_goal("g1", steps=[
            {"step": "a", "status": "completed"},
            {"step": "b", "status": "pending"},
        ])
        assert updated is not None
        assert len(await svc.list_goals()) == 1

    async def test_update_missing_returns_none(self, svc: MemoryService) -> None:
        assert await svc.update_goal("nonexistent", status="completed") is None
