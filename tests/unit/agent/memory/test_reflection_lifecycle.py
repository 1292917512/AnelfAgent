"""反思生命周期（reflection_lifecycle.py）单元测试。"""

from __future__ import annotations

from agent.memory import evidence
from agent.memory.memory_types import MemoryEntry, MemoryType
from agent.memory.reflection_lifecycle import (
    advance_reflections,
    get_lifecycle,
    reflection_status,
    seed_reflection,
)


def _reflection(content: str = "我发现主人最近睡眠变晚了", importance: float = 0.7) -> MemoryEntry:
    return MemoryEntry(
        memory_type=MemoryType.EPISODIC,
        content=content,
        tags=["type:reflection"],
        importance=importance,
        source="self_reflection",
    )


class TestSeed:
    def test_seeds_evidence_and_lifecycle(self):
        entry = _reflection(importance=0.9)
        assert seed_reflection(entry) is True
        assert reflection_status(entry) == "pending"
        assert evidence.get_evidence(entry.metadata)["reinforcement"] == 0.8

    def test_seed_idempotent(self):
        entry = _reflection()
        seed_reflection(entry)
        assert seed_reflection(entry) is False

    def test_non_reflection_not_seeded(self):
        entry = MemoryEntry(memory_type=MemoryType.SEMANTIC, content="事实", tags=[])
        assert seed_reflection(entry) is False


class TestAdvance:
    async def test_pending_to_confirmed_on_score(self, store, monkeypatch):
        monkeypatch.setattr(
            "agent.memory.reflection_lifecycle.get_config_int",
            lambda k, d: 0 if "cooldown" in k else d,
        )
        entry = _reflection(importance=0.9)  # 种子 0.8
        seed_reflection(entry)
        # 用户复述确认两次：0.8 → 2.8（连击前），越过 confirmed 阈值 1.0
        evidence.apply_reinforcement(entry.metadata, 1.0, user_originated=True)
        evidence.apply_reinforcement(entry.metadata, 1.0, user_originated=True)
        entry.id = await store.add(entry)

        report = await advance_reflections(store)
        assert report.confirmed == 1
        saved = await store.get(entry.id)
        assert reflection_status(saved) == "confirmed"

    async def test_below_threshold_stays_pending(self, store, monkeypatch):
        monkeypatch.setattr(
            "agent.memory.reflection_lifecycle.get_config_int",
            lambda k, d: 0 if "cooldown" in k else d,
        )
        entry = _reflection(importance=0.5)  # 种子 0
        seed_reflection(entry)
        entry.id = await store.add(entry)
        report = await advance_reflections(store)
        assert report.confirmed == 0
        assert reflection_status(await store.get(entry.id)) == "pending"

    async def test_terminal_entries_skipped(self, store):
        entry = _reflection()
        seed_reflection(entry)
        entry.metadata["lifecycle"] = {"status": "promoted"}
        entry.id = await store.add(entry)
        report = await advance_reflections(store)
        assert report.evaluated == 0

    async def test_promotion_writes_profile(self, store, monkeypatch):
        """confirmed 且 score 达标 → LLM 裁决 promote → 写入目标画像。"""
        monkeypatch.setattr(
            "agent.memory.reflection_lifecycle.get_config_int",
            lambda k, d: 0 if "cooldown" in k else d,
        )
        entry = _reflection(importance=0.9)
        seed_reflection(entry)
        for _ in range(2):
            evidence.apply_reinforcement(entry.metadata, 1.0, user_originated=True)
        # 先推进到 confirmed
        entry.id = await store.add(entry)
        await advance_reflections(store)
        saved = await store.get(entry.id)
        assert reflection_status(saved) == "confirmed"

        # 再补信号越过 promoted 阈值 2.0（当前 2.8 → 3.8）
        evidence.apply_reinforcement(saved.metadata, 1.0, user_originated=True)
        await store.update(saved)

        promoted_to: list = []

        async def _fake_llm(prompt: str, **kw):
            return '{"action": "promote", "content": "主人最近睡眠变晚", "reason": "新认知"}'

        async def _fake_update(store_, scope, content):
            promoted_to.append((scope, content))
            return True

        monkeypatch.setattr("agent.memory.dedup.light_llm", _fake_llm)
        monkeypatch.setattr(
            "agent.memory.self_profile.update_profile_content", _fake_update,
        )
        report = await advance_reflections(store)
        assert report.promoted == 1
        saved = await store.get(entry.id)
        assert reflection_status(saved) == "promoted"
        assert get_lifecycle(saved)["absorbed_into"].startswith("profile:")
        # 无归属标签的反思晋升到自画像
        assert promoted_to[0][0] == "agent:self"

    async def test_llm_reject_marks_denied(self, store, monkeypatch):
        monkeypatch.setattr(
            "agent.memory.reflection_lifecycle.get_config_int",
            lambda k, d: 0 if "cooldown" in k else d,
        )
        entry = _reflection(importance=0.95)
        seed_reflection(entry)
        for _ in range(3):
            evidence.apply_reinforcement(entry.metadata, 1.0, user_originated=True)
        entry.id = await store.add(entry)
        await advance_reflections(store)  # → confirmed

        saved = await store.get(entry.id)
        evidence.apply_reinforcement(saved.metadata, 1.0, user_originated=True)
        await store.update(saved)

        async def _reject(prompt: str, **kw):
            return '{"action": "reject", "reason": "一次性观察不值得固化"}'

        monkeypatch.setattr("agent.memory.dedup.light_llm", _reject)
        report = await advance_reflections(store)
        assert report.denied == 1
        assert reflection_status(await store.get(entry.id)) == "denied"

    async def test_llm_failure_never_silently_promotes(self, store, monkeypatch):
        """LLM 失败只退避重试，绝不静默晋升（防断电重复）。"""
        monkeypatch.setattr(
            "agent.memory.reflection_lifecycle.get_config_int",
            lambda k, d: 0 if "cooldown" in k else d,
        )
        entry = _reflection(importance=0.95)
        seed_reflection(entry)
        for _ in range(3):
            evidence.apply_reinforcement(entry.metadata, 1.0, user_originated=True)
        entry.id = await store.add(entry)
        await advance_reflections(store)  # confirmed
        saved = await store.get(entry.id)
        evidence.apply_reinforcement(saved.metadata, 1.0, user_originated=True)
        await store.update(saved)

        async def _boom(prompt: str, **kw):
            raise RuntimeError("LLM down")

        monkeypatch.setattr("agent.memory.dedup.light_llm", _boom)
        report = await advance_reflections(store)
        assert report.promoted == 0
        saved = await store.get(entry.id)
        assert reflection_status(saved) == "confirmed"  # 未被晋升
        assert get_lifecycle(saved)["promote_attempts"] == 1

    async def test_confirmed_block_renders(self, store):
        entry = _reflection(importance=0.95)
        seed_reflection(entry)
        entry.metadata["lifecycle"] = {"status": "confirmed"}
        entry.id = await store.add(entry)
        from agent.memory.reflection_lifecycle import load_confirmed_block
        block = await load_confirmed_block(store)
        assert "[系统注入·已确认认知]" in block
        assert entry.content[:30] in block

    async def test_confirmed_block_empty_when_none(self, store):
        from agent.memory.reflection_lifecycle import load_confirmed_block
        assert await load_confirmed_block(store) == ""
