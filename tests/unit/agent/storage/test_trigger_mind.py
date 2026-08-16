"""conversation_messages.trigger_mind 列与启动恢复过滤的回归测试。

回归场景（生产 bug）：require_mention 下非 @ 群消息运行时被 trigger_mind=False
挡在回复队列外，但仍写入对话历史；重启时 _do_recover_unanswered 扫描各 scope
最后一条消息，因无法区分是否触发思考，把非 @ 群消息补回队列导致误回复。
修复：入库记录 trigger_mind，恢复扫描据此排除。
"""

from __future__ import annotations

from agent.messages.everything import Everything
from agent.storage.data_center import ConversationData
from agent.storage.sqlite_backend import SqliteBackend
from agent.storage.storage_router import StorageRouter


class TestTriggerMindColumn:
    async def test_append_marks_trigger_mind_false(self, sqlite: SqliteBackend) -> None:
        """trigger_mind=False 的消息入库后可被恢复扫描识别。"""
        await sqlite.append_conversation(
            scope_type="group",
            scope_id="qq:390320020",
            role="user",
            content="[time:2026-08-08 10:00:00][uid:123] 群里闲聊",
            adapter_key="qq",
            trigger_mind=False,
        )
        rows = await sqlite.list_scopes_with_last_message()
        assert len(rows) == 1
        assert rows[0]["trigger_mind"] is False

    async def test_default_trigger_mind_true(self, sqlite: SqliteBackend) -> None:
        """默认 append（assistant/system/普通用户消息）视为触发思考。"""
        await sqlite.append_conversation(
            scope_type="user",
            scope_id="qq:123",
            role="user",
            content="[time:2026-08-08 10:00:00][uid:123] 你好",
            adapter_key="qq",
        )
        rows = await sqlite.list_scopes_with_last_message()
        assert rows[0]["trigger_mind"] is True

    async def test_last_message_flag_reflects_last_row(self, sqlite: SqliteBackend) -> None:
        """恢复扫描只看最后一条：最后是触发消息则补回，是非触发消息则跳过。"""
        base = dict(scope_type="group", scope_id="qq:1", role="user", adapter_key="qq")
        await sqlite.append_conversation(**base, content="[time:t1][uid:1] 闲聊", ts_ns=100, trigger_mind=False)
        await sqlite.append_conversation(**base, content="[time:t2][uid:1] @bot 在吗", ts_ns=200, trigger_mind=True)
        rows = await sqlite.list_scopes_with_last_message()
        assert rows[0]["trigger_mind"] is True

        await sqlite.append_conversation(
            **base, content="[time:t3][uid:1] 又没 @ 的闲聊", ts_ns=300, trigger_mind=False
        )
        rows = await sqlite.list_scopes_with_last_message()
        assert rows[0]["trigger_mind"] is False

    async def test_fresh_db_schema_complete(self, sqlite: SqliteBackend) -> None:
        """新库建表即含全部列（schema 一次到位，无懒迁移）。"""
        db = await sqlite._get_db()
        cursor = await db.execute("PRAGMA table_info(conversation_messages)")
        cols = {row[1] for row in await cursor.fetchall()}
        assert {
            "id",
            "scope_type",
            "scope_id",
            "role",
            "content",
            "ts_ns",
            "adapter_key",
            "trigger_mind",
            "embedding_blob",
        } <= cols


class TestDataCenterPassthrough:
    async def test_everything_trigger_mind_persisted(self, tmp_path) -> None:
        """data_center 入库链路把 Everything.trigger_mind 透传到存储层。"""
        sqlite = SqliteBackend(db_path=str(tmp_path / "agent.sqlite3"))
        try:
            conv = ConversationData(StorageRouter(sqlite=sqlite))
            await conv.add_conversation_record_by_everything(
                Everything(uid=123, adapter_key="qq", content="在吗", trigger_mind=False)
            )
            rows = await sqlite.list_scopes_with_last_message()
            assert len(rows) == 1
            assert rows[0]["trigger_mind"] is False
        finally:
            await sqlite.close()
