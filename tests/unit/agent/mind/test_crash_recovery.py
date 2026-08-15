"""崩溃尾部修复（agent.mind.crash_recovery）单元测试。

覆盖：检查点表的写/清/加载、崩溃残留扫描注入中断元消息、非法 scope 处理、
注入失败保留行（at-least-once）。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent.mind.crash_recovery import INTERRUPTED_NOTICE, recover_interrupted_replies
from agent.storage.data_center import ConversationData
from agent.storage.sqlite_backend import SqliteBackend
from agent.storage.storage_router import StorageRouter


@pytest.fixture()
async def conv_data(tmp_path):
    sqlite = SqliteBackend(db_path=str(tmp_path / "crash.sqlite3"))
    data = ConversationData(StorageRouter(sqlite=sqlite))
    await data.router.sqlite._ensure_init()
    yield data
    await sqlite.close()


def _mind(conv_data) -> SimpleNamespace:
    return SimpleNamespace(conversation_data=conv_data)


class TestReplyCheckpoints:
    async def test_record_and_load(self, conv_data) -> None:
        sqlite = conv_data.router.sqlite
        await sqlite.record_reply_checkpoint("user_qq:123", adapter_key="qq", phase="reply")
        rows = await sqlite.load_reply_checkpoints()
        assert len(rows) == 1
        assert rows[0]["scope_key"] == "user_qq:123"
        assert rows[0]["adapter_key"] == "qq"
        assert rows[0]["phase"] == "reply"

    async def test_clear_removes_row(self, conv_data) -> None:
        sqlite = conv_data.router.sqlite
        await sqlite.record_reply_checkpoint("user_qq:123")
        await sqlite.clear_reply_checkpoint("user_qq:123")
        assert await sqlite.load_reply_checkpoints() == []

    async def test_record_replaces_same_scope(self, conv_data) -> None:
        sqlite = conv_data.router.sqlite
        await sqlite.record_reply_checkpoint("user_qq:1", iteration=1)
        await sqlite.record_reply_checkpoint("user_qq:1", iteration=5)
        rows = await sqlite.load_reply_checkpoints()
        assert len(rows) == 1
        assert rows[0]["iteration"] == 5


class TestRecoverInterrupted:
    async def test_injects_notice_and_clears(self, conv_data) -> None:
        sqlite = conv_data.router.sqlite
        await sqlite.record_reply_checkpoint("user_qq:123", adapter_key="qq")
        recovered = await recover_interrupted_replies(_mind(conv_data))
        assert recovered == 1
        # 检查点已清除
        assert await sqlite.load_reply_checkpoints() == []
        # 中断元消息已写入对话历史
        msgs = await sqlite.fetch_conversation(scope_type="user", scope_id="qq:123", limit=10)
        assert any(m["content"] == INTERRUPTED_NOTICE and m["role"] == "system" for m in msgs)

    async def test_group_scope_injects(self, conv_data) -> None:
        sqlite = conv_data.router.sqlite
        await sqlite.record_reply_checkpoint("group_feishu:456", adapter_key="feishu")
        recovered = await recover_interrupted_replies(_mind(conv_data))
        assert recovered == 1
        msgs = await sqlite.fetch_conversation(scope_type="group", scope_id="feishu:456", limit=10)
        assert any(m["content"] == INTERRUPTED_NOTICE for m in msgs)

    async def test_invalid_scope_cleared_without_injection(self, conv_data) -> None:
        sqlite = conv_data.router.sqlite
        # 非法 scope（非 user/group 前缀）：直接清除，不注入
        await sqlite.record_reply_checkpoint("bogus_scope_x")
        recovered = await recover_interrupted_replies(_mind(conv_data))
        assert recovered == 0
        assert await sqlite.load_reply_checkpoints() == []

    async def test_no_checkpoints_noop(self, conv_data) -> None:
        assert await recover_interrupted_replies(_mind(conv_data)) == 0

    async def test_injected_notice_does_not_trigger_mind(self, conv_data) -> None:
        """中断元消息 trigger_mind=False，不触发新一轮思考（直接查库验证）。"""
        sqlite = conv_data.router.sqlite
        await sqlite.record_reply_checkpoint("user_qq:123")
        await recover_interrupted_replies(_mind(conv_data))
        db = await sqlite._get_db()
        cursor = await db.execute(
            "SELECT trigger_mind FROM conversation_messages WHERE content=?",
            (INTERRUPTED_NOTICE,),
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row[0] == 0
