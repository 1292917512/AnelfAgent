"""真实双 SQLite 库验证画像提交、恢复、身份隔离和并发边界。"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent.memory import evidence
from agent.memory.memory_types import MemoryEntry, MemoryType
from agent.memory.profiles import ProfileWriter
from agent.memory.reflection_lifecycle import advance_reflections, seed_reflection
from agent.storage.data_center import EverythingData
from agent.storage.profile_store import ProfileConflict
from agent.storage.sqlite_backend import SqliteBackend
from agent.storage.storage_router import StorageRouter


@pytest.fixture
async def profiles(store, tmp_path):
    sqlite = SqliteBackend(str(tmp_path / "agent.sqlite3"))
    writer = ProfileWriter(EverythingData(StorageRouter(sqlite)), store)
    yield writer
    await sqlite.close()


async def write(profiles, content, scope_type="user", scope_id="qq:1", **kwargs):
    current = await profiles.read(scope_type, scope_id)
    return await profiles.write(scope_type, scope_id, content, expected_revision=current["revision"], actor="test", **kwargs)


async def test_upgrade_preserves_legacy_rows_and_does_not_invent_history(tmp_path, store):
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA user_version=1")
        db.execute("CREATE TABLE entity_profile(scope_type TEXT,scope_id TEXT,personality TEXT,"
                   "updated_ts_ns INTEGER,conv_num INTEGER,conv_update_num INTEGER,PRIMARY KEY(scope_type,scope_id))")
        db.execute("INSERT INTO entity_profile VALUES('user','legacy-id','历史画像',123,900,17)")
    backend = SqliteBackend(str(path))
    writer = ProfileWriter(EverythingData(StorageRouter(backend)), store)
    try:
        assert (await backend.list_entity_profiles())[0] == {
            "scope_type": "user", "scope_id": "legacy-id", "personality": "历史画像",
            "updated_ts_ns": 123, "conv_num": 900, "conv_update_num": 17,
        }
        assert await writer.operations.timeline() == []
        assert await writer.recover() == 0
        assert await store.count() == 0
        await write(writer, "历史画像与新事实", scope_id="legacy-id")
        op = (await writer.operations.timeline())[0]
        assert op.before_content == "历史画像" and op.after_content == "历史画像与新事实"
        assert (await backend.get_entity_personality(scope_type="user", scope_id="legacy-id"))["conv_num"] == 900
    finally:
        await backend.close()


async def test_alias_updates_preserve_counters_mirror_ids_and_unrelated_group(profiles, store):
    await write(profiles, "用户原文")
    await write(profiles, "同号群组", scope_type="group")
    await profiles.sqlite.set_entity_personality(scope_type="user", scope_id="webui:2", personality="旧别名", conv_num=5)
    await profiles.sqlite.set_alias(scope_type="user", scope_id="webui:2", primary_scope_type="user", primary_scope_id="qq:1")
    user = await profiles.entities.get_anything(uid=1, adapter_key="qq")
    alias = await profiles.entities.get_anything(uid=2, adapter_key="webui")
    user.personality.update(conv_num=100, conv_update_num=7)
    alias.personality.update(conv_num=5, conv_update_num=3)
    await profiles.entities.save_all_entity_counters()
    mirror = (await store.search_by_tags(["user:qq:1"]))[0]
    mirror.metadata["origins"] = [{"kind": "legacy"}]
    await store.update(mirror)
    await write(profiles, "用户新认知", scope_id="webui:2")
    assert user.personality["personality"] == alias.personality["personality"] == "用户新认知"
    assert user.personality["conv_num"] == 100 and alias.personality["conv_num"] == 5
    assert alias.personality["conv_update_num"] == 3
    saved = await store.get(mirror.id)
    assert saved.content == "用户新认知" and saved.metadata == mirror.metadata
    assert (await store.search_by_tags(["group:qq:1"]))[0].content == "同号群组"
    assert (await profiles.sqlite.get_entity_personality(scope_type="user", scope_id="qq:1"))["conv_num"] == 100


async def test_noop_and_parallel_cas_are_observable_without_empty_revisions(profiles, store):
    await write(profiles, "原文")
    baseline = await profiles.read("user", "qq:1")
    before = await store.list_audit()
    assert not (await write(profiles, "原文")).changed
    assert await store.list_audit() == before
    results = await asyncio.gather(*[
        profiles.write("user", "qq:1", value, expected_revision=baseline["revision"], actor="test")
        for value in ("新认识一", "新认识二")
    ], return_exceptions=True)
    assert sum(isinstance(result, ProfileConflict) for result in results) == 1
    assert len(await profiles.operations.timeline()) == 2
    assert len(await profiles.operations.pending()) == 0


async def test_journal_failure_rolls_back_body(profiles, monkeypatch):
    await write(profiles, "提交前")
    db = await profiles.sqlite._get_db()
    execute = db.execute

    async def fail(sql, *args, **kwargs):
        if sql.startswith("INSERT INTO profile_operations"):
            raise RuntimeError("journal failure")
        return await execute(sql, *args, **kwargs)

    monkeypatch.setattr(db, "execute", fail)
    with pytest.raises(RuntimeError, match="journal failure"):
        await write(profiles, "不应留下半成品")
    assert (await profiles.read("user", "qq:1"))["personality"] == "提交前"
    assert len(await profiles.operations.timeline()) == 1


async def test_committed_sync_failure_survives_reopen_and_old_retry_cannot_overwrite_new_profile(profiles, store, monkeypatch):
    await write(profiles, "原文")
    mirror_id = (await store.search_by_tags(["user:qq:1"]))[0].id
    with monkeypatch.context() as patch:
        patch.setattr(profiles, "_sync_mirror", AsyncMock(side_effect=RuntimeError("memory unavailable")))
        result = await write(profiles, "新认知", operation_id="stable-operation")
    assert result.synced is False
    assert (await profiles.operations.get_operation(result.operation_id)).error
    await profiles.sqlite.close()
    reopened = SqliteBackend(profiles.sqlite.db_path)
    writer = ProfileWriter(EverythingData(StorageRouter(reopened)), store)
    try:
        assert await writer.recover() == 1
        assert (await store.get(mirror_id)).content == "新认知"
        await write(writer, "后来修订")
        resumed = await writer.resume(result.operation_id)
        assert resumed.synced and not resumed.changed
        assert (await writer.read("user", "qq:1"))["personality"] == "后来修订"
        assert (await store.get(mirror_id)).content == "后来修订"
    finally:
        await reopened.close()


async def test_reflection_commits_once_then_finishes_after_sync_recovers(profiles, store, monkeypatch):
    entry = MemoryEntry(memory_type=MemoryType.REFLECTION, content="我应依据证据核验再回答", tags=["type:reflection"])
    seed_reflection(entry)
    evidence.apply_reinforcement(entry.metadata, 4, user_originated=True)
    entry.metadata["lifecycle"]["status"] = "confirmed"
    entry.id = await store.add(entry)
    model = AsyncMock(side_effect=[
        json.dumps({"target": "agent:self", "reason": "自身稳定行为"}),
        json.dumps({"action": "promote", "content": "依据证据核验再回答", "reason": "多次反馈确认"}),
    ])
    monkeypatch.setattr("agent.memory.dedup.light_llm", model)
    with monkeypatch.context() as patch:
        patch.setattr(profiles, "_sync_mirror", AsyncMock(side_effect=RuntimeError("offline")))
        assert (await advance_reflections(store, profiles)).promoted == 0
    saved = await store.get(entry.id)
    assert saved.metadata["lifecycle"]["status"] == "confirmed"
    await profiles.sqlite.close()
    assert (await advance_reflections(store, profiles)).promoted == 1
    assert model.await_count == 2
    assert len(await profiles.operations.timeline()) == 1
    assert (await profiles.read("agent", "self"))["personality"] == "依据证据核验再回答"
    assert (await advance_reflections(store, profiles)).promoted == 0
    assert model.await_count == 2


async def test_stale_source_version_cannot_write_profile(profiles, store):
    mid = await store.add(MemoryEntry(memory_type=MemoryType.REFLECTION, content="已被推翻的认识"))
    entry = await store.get(mid)
    old_version = entry.version
    entry.content = "更正后的认识"
    await store.update(entry)
    with pytest.raises(ValueError, match="反思来源已变化"):
        await write(profiles, "不应该提交", source_memory_id=mid, source_version=old_version)
    assert await profiles.operations.timeline() == []


async def test_memory_noop_preserves_version_vectors_audit_and_outbox(store):
    mid = await store.add(MemoryEntry(memory_type=MemoryType.SEMANTIC, content="事实", embedding=[1, 0]))
    entry = await store.get(mid)
    before = await store.list_audit()
    assert await store.update(entry, expected_version=entry.version)
    assert await store.get(mid) == entry
    assert await store.list_audit() == before
    assert not await store.update(entry, expected_version=entry.version - 1)


async def test_task_retention_uses_existing_judgment_and_does_not_default_to_memory(monkeypatch):
    from agent.judgment import ChoiceAnswer
    from agent.memory.dedup import judge_write

    for choice, expected in (("execution_only", "skip"), ("durable", "store")):
        engine = SimpleNamespace(judge=AsyncMock(return_value=SimpleNamespace(answers={
            "retention": ChoiceAnswer(choice=choice, probabilities={choice: 1}, confidence=1),
        })))
        monkeypatch.setattr("agent.memory.dedup.get_judgment_engine", lambda: engine)
        assert (await judge_write("任务产出", [], task_result=True))["action"] == expected
        assert "retention" in engine.judge.await_args.args[1]
    engine.judge.side_effect = RuntimeError("model unavailable")
    assert (await judge_write("待判断的任务产出", [], task_result=True))["action"] == "skip"
    assert (await judge_write("普通事实", []))["action"] == "store"


async def test_history_limits_keep_pending_receipts_and_latest_body(profiles, monkeypatch):
    for index in range(5):
        await write(profiles, f"认知 {index}")
    with monkeypatch.context() as patch:
        patch.setattr(profiles, "_sync_mirror", AsyncMock(side_effect=RuntimeError("offline")))
        pending = await write(profiles, "等待恢复")
    await profiles.operations.prune(days=90, per_profile=2, total=2)
    timeline = await profiles.operations.timeline()
    assert len(timeline) == 3
    assert timeline[0].operation_id == pending.operation_id and not timeline[0].synced
    assert (await profiles.read("user", "qq:1"))["personality"] == "等待恢复"
    assert (await profiles.resume(pending.operation_id)).synced
    await profiles.operations.prune(days=90, per_profile=2, total=1)
    assert len(await profiles.operations.timeline()) == 1


async def test_retry_after_alias_link_does_not_restore_stale_online_profile(profiles, monkeypatch):
    await write(profiles, "主身份", scope_id="qq:1")
    with monkeypatch.context() as patch:
        patch.setattr(profiles, "_sync_mirror", AsyncMock(side_effect=RuntimeError("offline")))
        pending = await write(profiles, "关联前的旧身份", scope_id="webui:2")
    await profiles.sqlite.set_alias(scope_type="user", scope_id="webui:2", primary_scope_type="user", primary_scope_id="qq:1")
    alias = await profiles.entities.get_anything(uid=2, adapter_key="webui")
    assert (await profiles.resume(pending.operation_id)).synced
    assert alias.personality["personality"] == "主身份"


async def test_ai_can_control_self_profile_and_read_receipts(profiles, monkeypatch):
    from agent.memory import profile_tools, tools
    runtime = SimpleNamespace(data_center=SimpleNamespace(everything_data=profiles.entities), memory_store=profiles.store)
    monkeypatch.setattr("agent.runtime.singleton.require_runtime", lambda: runtime)
    monkeypatch.setattr(tools, "_get_sqlite", lambda: profiles.sqlite)
    baseline = json.loads(await tools.get_entity_profile("agent", "self"))
    saved = json.loads(await tools.update_entity_profile("agent", "self", "自主修订的认知", baseline["revision"], "核验事实"))
    assert saved["ok"] and saved["synced"]
    detail = json.loads(await profile_tools.profile_history(operation_id=saved["operation_id"]))
    assert detail["after_content"] == "自主修订的认知" and detail["reason"] == "核验事实"
    assert json.loads(await profile_tools.sync_profiles(saved["operation_id"]))["synced"]
    removed = json.loads(await tools.delete_entity_profile("agent", "self", saved["revision"]))
    assert removed["ok"] and removed["synced"]
    history = json.loads(await profile_tools.memory_history(limit=1))
    assert len(history["items"]) == 1 and history["next_cursor"]


async def test_failed_main_commit_reuses_persisted_promotion_decision(profiles, store, monkeypatch):
    from agent.memory.promotion import promote_reflection
    entry = MemoryEntry(memory_type=MemoryType.REFLECTION, content="稳定认知", tags=["type:reflection"])
    seed_reflection(entry)
    evidence.apply_reinforcement(entry.metadata, 4, user_originated=True)
    entry.metadata["lifecycle"]["status"] = "confirmed"
    entry.id = await store.add(entry)
    model = AsyncMock(side_effect=[
        json.dumps({"target": "agent:self", "reason": "行为经验"}),
        json.dumps({"action": "promote", "content": "稳定认知", "reason": "充分证据"}),
    ])
    monkeypatch.setattr("agent.memory.dedup.light_llm", model)
    with monkeypatch.context() as patch:
        patch.setattr(profiles.operations, "commit", AsyncMock(side_effect=RuntimeError("disk unavailable")))
        assert (await advance_reflections(store, profiles)).promoted == 0
    current = await store.get(entry.id)
    assert await promote_reflection(store, profiles, current, current.metadata["lifecycle"]) == "promoted"
    assert model.await_count == 2
    assert len(await profiles.operations.timeline()) == 1


async def test_stale_archive_does_not_remove_new_feedback(store):
    mid = await store.add(MemoryEntry(memory_type=MemoryType.REFLECTION, content="待确认认知"))
    entry = await store.get(mid)
    old_version = entry.version
    evidence.apply_reinforcement(entry.metadata, 4, user_originated=True)
    await store.update(entry)
    assert not await store.archive_memory(mid, expected_version=old_version)
    assert (await store.get(mid)).metadata == entry.metadata


async def test_retention_uses_registered_config_and_environment_without_changing_body(profiles, monkeypatch):
    import time
    from pathlib import Path

    from core.config import ConfigManager, ConfigRegistry

    item = ConfigRegistry.get_item("profile_history_days")
    assert item.group == "memory/profile_history" and item.default_value == 30
    first = await write(profiles, "旧版本")
    second = await write(profiles, "当前事实")
    async with profiles.sqlite._write_tx() as db:
        await db.execute("UPDATE profile_operations SET created_at=? WHERE operation_id=?",
                         (time.time() - 31 * 86400, first.operation_id))
        await db.execute("UPDATE profile_operations SET created_at=? WHERE operation_id=?",
                         (time.time() - 21 * 86400, second.operation_id))
        await db.commit()
    monkeypatch.setenv("ANELF_PROFILE_HISTORY_DAYS", "30")
    ConfigManager.set_persisted({"profile_history_days": 7})
    assert ConfigManager.get("profile_history_days") == 30
    assert json.loads(Path(ConfigManager._get_config_file()).read_text(encoding="utf-8"))["profile_history_days"] == 7
    await profiles.recover()
    assert [op.operation_id for op in await profiles.operations.timeline()] == [second.operation_id]
    assert (await profiles.read("user", "qq:1"))["personality"] == "当前事实"
    monkeypatch.delenv("ANELF_PROFILE_HISTORY_DAYS")
    ConfigManager.reload()
    await profiles.recover()
    assert await profiles.operations.timeline() == []
    assert (await profiles.read("user", "qq:1"))["personality"] == "当前事实"
