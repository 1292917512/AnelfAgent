"""实时会话窗口的并发、内存边界和持久化一致性。"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent.messages import Everything, EverythingGroup, MessageUser
from agent.storage.data_center import ConversationData
from agent.storage.sqlite_backend import SqliteBackend
from agent.storage.storage_router import StorageRouter


@pytest.mark.parametrize("kind", ["chat", "notification"])
async def test_replay_dedup_is_atomic_and_ignores_only_arrival_time(sqlite, kind):
    data = ConversationData(StorageRouter(sqlite=sqlite))
    first = MessageUser(uid="1", adapter_key="qq", adapter_message_id="id_%_1", message_kind=kind,
                        text_content="[time:正文时间][uid:别人]重复网络投递", created_ts_ns=time.time_ns())
    await read(sqlite)
    copies = [first.model_copy(update={"created_ts_ns": first.created_ts_ns + i * 3_000_000_000})
              for i in range(8)]
    assert sum(await asyncio.gather(*(data.add_conversation_record_by_everything(m) for m in copies))) == 1
    assert len(await read(sqlite)) == 1
    await sqlite.close()
    assert await data.add_conversation_record_by_everything(copies[-1]) is False
    variants = [
        {"adapter_message_id": "another_id"},  # 相同正文的人为重发
        {"text_content": "[time:编辑时间][uid:别人]重复网络投递"},  # 同 ID 的编辑
        {"session_id": "another_session"},
        {"adapter_key": "telegram"},
    ]
    for change in variants:
        assert await data.add_conversation_record_by_everything(first.model_copy(update=change)) is True
    no_id = first.model_copy(update={"adapter_message_id": ""})
    assert await data.add_conversation_record_by_everything(no_id) is True
    assert await data.add_conversation_record_by_everything(no_id) is True


async def test_dedup_query_failure_does_not_admit_an_unchecked_message(sqlite, monkeypatch):
    data = ConversationData(StorageRouter(sqlite=sqlite))
    message = MessageUser(uid="1", adapter_key="qq", adapter_message_id="m1", text_content="执行一次")
    monkeypatch.setattr(sqlite, "_conversation_has_duplicate", AsyncMock(side_effect=OSError("lookup failed")))
    with pytest.raises(OSError, match="lookup failed"):
        await data.add_conversation_record_by_everything(message)
    assert await read(sqlite) == []


async def test_message_lookup_uses_original_id_not_copied_body_tags(sqlite):
    data = ConversationData(StorageRouter(sqlite=sqlite))
    message = MessageUser(uid="1", adapter_key="qq", adapter_message_id="original",
                          text_content="[message_id:copied][reply_to:copied-ref]复制的聊天记录")
    await data.add_conversation_record_by_everything(message)
    assert await sqlite.find_conversation_by_message_id("copied") == []
    hits = await sqlite.find_conversation_by_message_id("original")
    assert len(hits) == 1 and hits[0]["reply_to"] == ""


async def append(sqlite: SqliteBackend, text: str, *, scope: str = "qq:1", role: str = "user", ts: int = 100) -> None:
    await sqlite.append_conversation(scope_type="user", scope_id=scope, role=role, content=text, ts_ns=ts)


async def read(sqlite: SqliteBackend, *, scope: str = "qq:1", after: int | None = None, limit: int | None = 30) -> list:
    return await sqlite.fetch_live_conversation(scope_type="user", scope_id=scope, after_id=after, limit=limit)


@pytest.mark.parametrize("group", [False, True])
@pytest.mark.parametrize("role", ["system", "assistant"])
async def test_internal_context_keeps_route_and_live_window(sqlite, monkeypatch, group, role):
    data = ConversationData(StorageRouter(sqlite=sqlite))
    target = (EverythingGroup(uid="u1", group_id="g1", adapter_key="webui", session_id="one")
              if group else Everything(uid="u1", adapter_key="webui", session_id="one"))
    other = target.model_copy(update={"session_id": "two"})
    await sqlite.fetch_live_conversation(scope_type=target.scope_type, scope_id=target.scope_id, limit=10)
    await data.append_context(other, "另一会话")
    await data.append_context(target, "原始上下文内容", role=role)
    persisted = await sqlite.fetch_conversation(scope_type=target.scope_type, scope_id=target.scope_id, limit=10)
    assert [(row["role"], row["content"]) for row in persisted] == [(role, "原始上下文内容")]
    monkeypatch.setattr(sqlite, "_get_db", AsyncMock(side_effect=AssertionError("live window missed context")))
    live = await sqlite.fetch_live_conversation(scope_type=target.scope_type, scope_id=target.scope_id, limit=10)
    assert [(row["role"], row["content"]) for row in live] == [(role, "原始上下文内容")]


async def test_warm_reads_need_no_database_and_snapshots_are_independent(sqlite, monkeypatch):
    assert await read(sqlite) == []
    await append(sqlite, "received")
    await append(sqlite, "sent", role="assistant", ts=99)
    await append(sqlite, "fact", role="system")
    monkeypatch.setattr(sqlite, "_get_db", AsyncMock(side_effect=AssertionError("hot read accessed database")))

    rows = await read(sqlite, after=1)
    assert [row["content"] for row in rows] == ["sent", "fact"]
    rows[0]["content"] = "changed copy"
    assert [row["content"] for row in await read(sqlite, after=1)] == ["sent", "fact"]
    assert len(await read(sqlite)) == 3


async def test_cold_load_racing_with_writes_does_not_lose_or_duplicate_messages(sqlite, monkeypatch):
    await append(sqlite, "initial")
    db = await sqlite._get_db()
    execute = db.execute
    entered, release = asyncio.Event(), asyncio.Event()

    async def paused_execute(sql, *args, **kwargs):
        result = await execute(sql, *args, **kwargs)
        if "ORDER BY id DESC LIMIT" in sql:
            entered.set()
            await release.wait()
        return result

    monkeypatch.setattr(db, "execute", paused_execute)
    load = asyncio.create_task(read(sqlite))
    await asyncio.wait_for(entered.wait(), 2)
    writers = [asyncio.create_task(append(sqlite, str(i))) for i in range(12)]
    release.set()
    await asyncio.gather(load, *writers)
    rows = await read(sqlite, after=0)
    assert len(rows) == 13
    assert len({row["id"] for row in rows}) == 13
    assert {row["content"] for row in rows} == {"initial", *(str(i) for i in range(12))}


async def test_scope_isolation_eviction_and_restart(sqlite, monkeypatch):
    monkeypatch.setattr("agent.storage.sqlite_backend._LIVE_SCOPES", 2)
    scopes = ["qq:1", "webui:1#a", "webui:1#b"]
    for scope in scopes:
        await append(sqlite, scope, scope=scope)
        assert [row["content"] for row in await read(sqlite, scope=scope)] == [scope]
    assert len(sqlite._conversation_windows) == 2
    assert [row["content"] for row in await read(sqlite)] == ["qq:1"]
    await sqlite.close()
    assert not sqlite._conversation_windows
    restored = SqliteBackend(db_path=sqlite.db_path)
    try:
        assert [row["content"] for row in await read(restored, scope=scopes[2])] == [scopes[2]]
    finally:
        await restored.close()


async def test_lagging_reader_recovers_all_messages_after_window_eviction(sqlite, monkeypatch):
    monkeypatch.setattr("agent.storage.sqlite_backend._LIVE_MESSAGES", 3)
    await read(sqlite)
    for i in range(9):
        await append(sqlite, str(i))
    assert len(sqlite._conversation_windows[("user", "qq:1")].rows) == 3
    contents, cursor = [], 0
    while rows := await read(sqlite, after=cursor, limit=2):
        contents.extend(row["content"] for row in rows)
        cursor = rows[-1]["id"]
    assert contents == [str(i) for i in range(9)]
    monkeypatch.setattr(sqlite, "_get_db", AsyncMock(side_effect=AssertionError("caught-up reader accessed database")))
    assert await read(sqlite, after=cursor) == []


@pytest.mark.parametrize("window_size", [3, 256])
async def test_complete_unread_snapshot_keeps_latest_correction_and_watermark(sqlite, monkeypatch, window_size):
    monkeypatch.setattr("agent.storage.sqlite_backend._LIVE_MESSAGES", window_size)
    await read(sqlite)
    await append(sqlite, "already seen")
    for index in range(30):
        await append(sqlite, str(index), ts=101 + index)
    await append(sqlite, "stop before executing", ts=150)
    rows = await read(sqlite, after=1, limit=None)
    assert [row["content"] for row in rows] == [*(str(index) for index in range(30)), "stop before executing"]
    await append(sqlite, "arrived after snapshot", ts=151)
    assert [row["content"] for row in await read(sqlite, after=rows[-1]["id"], limit=None)] == ["arrived after snapshot"]
    assert rows[-1]["content"] == "stop before executing"


@pytest.mark.parametrize("cold", [False, True])
async def test_timestamp_reader_uses_memory_only_when_all_unread_rows_are_covered(sqlite, monkeypatch, cold):
    monkeypatch.setattr("agent.storage.sqlite_backend._LIVE_MESSAGES", 3)
    await read(sqlite)
    for index, ts in enumerate([1000, 200, 300, 400, 500, 1100]):
        await append(sqlite, str(index), ts=ts)
    if cold:
        sqlite.invalidate_conversation_windows()
    rows = await sqlite.fetch_live_conversation(scope_type="user", scope_id="qq:1", limit=None,
                                                after_id=0, after_ts=900)
    assert [row["content"] for row in rows] == ["0", "5"]
    monkeypatch.setattr(sqlite, "_get_db", AsyncMock(side_effect=AssertionError("fresh notification queried database")))
    rows = await sqlite.fetch_live_conversation(scope_type="user", scope_id="qq:1", limit=None,
                                                after_id=0, after_ts=1000)
    assert [row["content"] for row in rows] == ["5"]


async def test_large_messages_are_not_truncated_or_retained_unbounded(sqlite, monkeypatch):
    monkeypatch.setattr("agent.storage.sqlite_backend._LIVE_CHARS", 5)
    await read(sqlite)
    await append(sqlite, "large message")
    await append(sqlite, "small")
    assert sum(len(row["content"]) for row in sqlite._conversation_windows[("user", "qq:1")].rows) <= 5
    assert [row["content"] for row in await read(sqlite, after=0)] == ["large message", "small"]


async def test_edit_delete_clear_and_sweep_do_not_resurrect_stale_rows(sqlite):
    await append(sqlite, "old")
    await append(sqlite, "delete me")
    assert len(await read(sqlite)) == 2
    assert await sqlite.update_conversation_message(1, "edited")
    assert (await read(sqlite))[0]["content"] == "edited"
    await sqlite.delete_conversation_by_id(2)
    assert [row["content"] for row in await read(sqlite)] == ["edited"]
    assert await sqlite.clear_conversation(scope_type="user", scope_id="qq:1") == 1
    assert await read(sqlite) == []
    await append(sqlite, "orphan", role="system")
    assert len(await read(sqlite)) == 1
    await sqlite.sweep_empty_conversations(min_age_seconds=0)
    assert await read(sqlite) == []


async def test_failed_write_is_not_published(sqlite, monkeypatch):
    await append(sqlite, "committed")
    await read(sqlite)
    db = await sqlite._get_db()
    with monkeypatch.context() as patch:
        patch.setattr(db, "commit", AsyncMock(side_effect=OSError("disk full")))
        with pytest.raises(OSError, match="disk full"):
            await append(sqlite, "uncommitted")
    assert [row["content"] for row in await read(sqlite)] == ["committed"]


async def test_database_editor_invalidates_runtime_window(sqlite, monkeypatch):
    from services.database import DatabaseService

    service = DatabaseService()
    monkeypatch.setattr(service, "_db_path", lambda _db_id: sqlite.db_path)
    monkeypatch.setattr("services._runtime.get_runtime", lambda: SimpleNamespace(data_center=SimpleNamespace(sqlite=sqlite)))
    try:
        await append(sqlite, "before")
        await read(sqlite)
        await service.update_row("agent", "conversation_messages", 1, {"content": "after"})
        assert [row["content"] for row in await read(sqlite)] == ["after"]
        await service.insert_row("agent", "conversation_messages", {
            "scope_type": "user", "scope_id": "qq:1", "role": "system", "content": "inserted", "ts_ns": 101,
        })
        assert len(await read(sqlite)) == 2
        await service.delete_row("agent", "conversation_messages", 1)
        assert [row["content"] for row in await read(sqlite)] == ["inserted"]
    finally:
        await service.close_all()


async def test_external_edit_during_cold_load_cannot_publish_stale_snapshot(sqlite, monkeypatch):
    await append(sqlite, "before")
    db = await sqlite._get_db()
    execute = db.execute
    edited = False

    async def edit_after_read(sql, *args, **kwargs):
        nonlocal edited
        cursor = await execute(sql, *args, **kwargs)
        if "ORDER BY id DESC LIMIT" in sql and not edited:
            records = list(await cursor.fetchall())
            edited = True
            await execute("UPDATE conversation_messages SET content='after' WHERE id=1")
            await db.commit()
            sqlite.invalidate_conversation_windows()
            return SimpleNamespace(fetchall=AsyncMock(return_value=records))
        return cursor

    monkeypatch.setattr(db, "execute", edit_after_read)
    assert [row["content"] for row in await read(sqlite)] == ["after"]


@pytest.mark.parametrize("editor", [False, True])
@pytest.mark.parametrize("committed", [False, True])
async def test_cancellation_at_commit_keeps_window_consistent(sqlite, monkeypatch, editor, committed):
    from services.database import DatabaseService

    await append(sqlite, "before")
    await read(sqlite)
    service = DatabaseService()
    monkeypatch.setattr(service, "_db_path", lambda _db_id: sqlite.db_path)
    monkeypatch.setattr("services._runtime.get_runtime", lambda: SimpleNamespace(data_center=SimpleNamespace(sqlite=sqlite)))
    db = await service._get_conn("agent") if editor else await sqlite._get_db()
    commit = db.commit
    entered = asyncio.Event()

    async def paused_commit():
        if committed:
            await commit()
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(db, "commit", paused_commit)
    edit = (service.update_row("agent", "conversation_messages", 1, {"content": "after"})
            if editor else sqlite.update_conversation_message(1, "after"))
    task = asyncio.create_task(edit)
    try:
        await asyncio.wait_for(entered.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert [row["content"] for row in await read(sqlite)] == ["after" if committed else "before"]
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await service.close_all()
